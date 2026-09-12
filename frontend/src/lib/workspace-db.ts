import "server-only";

import { readFileSync } from "node:fs";
import { isIP } from "node:net";
import { checkServerIdentity } from "node:tls";
import { Pool, type PoolClient, type PoolConfig, type QueryResult, type QueryResultRow } from "pg";
import { nextLoginRateLimit } from "./login-rate-limit-core.ts";

const STORE_UNAVAILABLE = "Le stockage PostgreSQL est temporairement indisponible.";
const CONFIG_INVALID = "La connexion PostgreSQL doit être complète et vérifier TLS hors des tests locaux.";
const WORKSPACE_TABLES = ["profile", "leads", "suppressions", "campaigns", "drafts", "events", "login_rate_limit"];

export class WorkspaceStoreError extends Error {
  constructor(message = STORE_UNAVAILABLE) {
    super(message);
    this.name = "WorkspaceStoreError";
  }
}

type WorkspaceEnvironment = Partial<Pick<NodeJS.ProcessEnv, "WORKSPACE_DATABASE_URL" | "WORKSPACE_DATABASE_SSL_ROOT_CERT" | "NODE_ENV" | "APP_ENV">>;

/** Parse independently of pg's URL parser so query parameters cannot override TLS or the host. */
export function workspacePoolOptions(env: WorkspaceEnvironment = process.env): PoolConfig {
  try {
    const source = env.WORKSPACE_DATABASE_URL;
    if (!source || /\s/.test(source)) throw new Error();
    const url = new URL(source);
    const host = url.hostname.replace(/^\[|\]$/g, "");
    const user = decodeURIComponent(url.username);
    const password = decodeURIComponent(url.password);
    const database = decodeURIComponent(url.pathname.slice(1));
    const port = url.port ? Number(url.port) : 5432;
    if (!["postgres:", "postgresql:"].includes(url.protocol) || !host || !user || !password || !database || url.hash
      || url.pathname.slice(1).includes("/") || !Number.isInteger(port) || port < 1 || port > 65535
      || [user, password, database].some((value) => value.includes("\0"))) throw new Error();
    for (const key of url.searchParams.keys()) {
      if (!["sslmode", "sslrootcert", "channel_binding"].includes(key) || url.searchParams.getAll(key).length !== 1) throw new Error();
    }
    const loopback = host === "localhost" || host === "::1" || (isIP(host) === 4 && host.startsWith("127."));
    const production = env.NODE_ENV === "production" || env.APP_ENV === "production";
    const mode = url.searchParams.get("sslmode") ?? "verify-full";
    if (mode !== "verify-full" && !(mode === "disable" && loopback && !production)) throw new Error();
    // pg can prefer channel binding but does not implement libpq's require guarantee.
    const channelBinding = url.searchParams.get("channel_binding") ?? "prefer";
    if (!["disable", "prefer"].includes(channelBinding)) throw new Error();
    const rootCert = env.WORKSPACE_DATABASE_SSL_ROOT_CERT || url.searchParams.get("sslrootcert");
    if (mode === "disable" && rootCert) throw new Error();
    const options: PoolConfig = {
      host, port, user, database,
      ssl: mode === "disable" ? false : {
        rejectUnauthorized: true,
        checkServerIdentity: (_name, certificate) => checkServerIdentity(host, certificate),
        ...(rootCert ? { ca: readFileSync(rootCert, "utf8") } : {}),
      },
      enableChannelBinding: channelBinding === "prefer",
      // Do not inherit PGOPTIONS or PGSSLNEGOTIATION from an unrelated deployment.
      options: "-c client_encoding=UTF8",
      sslnegotiation: "postgres",
      application_name: "outreached-workspace",
      max: 1, min: 0, maxLifetimeSeconds: 300,
      idleTimeoutMillis: 30_000, connectionTimeoutMillis: 5_000,
      query_timeout: 18_000, allowExitOnIdle: true,
    };
    // pg preserves this non-enumerable property through Pool -> Client configuration.
    Object.defineProperty(options, "password", { value: password, enumerable: false });
    return options;
  } catch {
    throw new WorkspaceStoreError(CONFIG_INVALID);
  }
}

let cachedPool: { url: string; rootCert: string; production: boolean; pool: Pool } | undefined;

function workspacePool(): Pool {
  const url = process.env.WORKSPACE_DATABASE_URL ?? "";
  const rootCert = process.env.WORKSPACE_DATABASE_SSL_ROOT_CERT ?? "";
  const production = process.env.NODE_ENV === "production" || process.env.APP_ENV === "production";
  if (cachedPool && cachedPool.url === url && cachedPool.rootCert === rootCert && cachedPool.production === production) return cachedPool.pool;
  const pool = new Pool(workspacePoolOptions());
  // Idle socket failures are handled by pg's pool eviction, without logging credentials.
  pool.on("error", () => {});
  const previous = cachedPool;
  cachedPool = { url, rootCert, production, pool };
  if (previous) void previous.pool.end().catch(() => {});
  return pool;
}

/** Useful for process shutdown and isolated integration checks. */
export async function closeWorkspacePool(): Promise<void> {
  const previous = cachedPool;
  cachedPool = undefined;
  if (previous) await previous.pool.end();
}

export interface WorkspaceDatabase {
  query<Row extends QueryResultRow = QueryResultRow>(sql: string, values?: unknown[]): Promise<QueryResult<Row>>;
}

async function validateWorkspace(client: PoolClient): Promise<void> {
  const result = await client.query(`
    SELECT current_user AS role,
      has_schema_privilege(current_user, 'outreached_workspace', 'CREATE') AS can_create,
      EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='outreached_workspace' AND r.rolname=current_user) AS owns_objects,
      EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=current_user AND
        (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls OR r.rolreplication
         OR EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid))) AS privileged,
      (SELECT version FROM outreached_workspace.schema_version WHERE id=1) AS version,
      (SELECT state FROM outreached_workspace.login_rate_limit WHERE id=1) AS limiter_state,
      (SELECT COUNT(*)::integer FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='outreached_workspace' AND c.relname=ANY($1::text[]) AND c.relkind='r'
        AND c.relrowsecurity AND has_table_privilege(current_user,c.oid,'SELECT')
        AND has_table_privilege(current_user,c.oid,'INSERT') AND has_table_privilege(current_user,c.oid,'UPDATE')
        AND NOT has_table_privilege(current_user,c.oid,'DELETE')
        AND NOT has_table_privilege(current_user,c.oid,'TRUNCATE')) AS tables
  `, [WORKSPACE_TABLES]);
  const row = result.rows[0];
  if (!row || row.role !== "outreached_app" || row.can_create || row.owns_objects || row.privileged || row.version !== 1 || row.tables !== WORKSPACE_TABLES.length) {
    throw new WorkspaceStoreError("La migration et le rôle PostgreSQL restreint sont requis.");
  }
  // Parse a copy only: readiness must detect missing/corrupt shared auth state without spending attempts.
  nextLoginRateLimit(row.limiter_state, null);
}

/** Every writer sees the previous writer's commit before its first business read. */
export async function withWorkspace<T>(write: boolean, callback: (db: WorkspaceDatabase) => Promise<T>): Promise<T> {
  let client: PoolClient;
  try {
    client = await workspacePool().connect();
  } catch {
    throw new WorkspaceStoreError();
  }
  let destroy = false;
  // A server may close an idle borrowed connection while the callback is doing local work.
  // Handle that event too; pg's pool listener is present only while the client is checked in.
  const disconnected = () => { destroy = true; };
  client.on("error", disconnected);
  const db: WorkspaceDatabase = {
    async query<Row extends QueryResultRow = QueryResultRow>(sql: string, values?: unknown[]): Promise<QueryResult<Row>> {
      try { return await client.query<Row>(sql, values); }
      catch { throw new WorkspaceStoreError(); }
    },
  };
  try {
    try {
      await client.query(write ? "BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE" : "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
      await client.query("SET LOCAL search_path = outreached_workspace, pg_catalog; SET LOCAL statement_timeout = '15000ms'; SET LOCAL lock_timeout = '5000ms'; SET LOCAL idle_in_transaction_session_timeout = '20000ms'");
      if (write) await client.query("SELECT pg_advisory_xact_lock($1,$2)", [1869968498, 1]);
      await validateWorkspace(client);
    } catch { throw new WorkspaceStoreError(); }
    const result = await callback(db);
    if (destroy) throw new WorkspaceStoreError();
    try { await client.query("COMMIT"); }
    catch { throw new WorkspaceStoreError(); }
    return result;
  } catch (error) {
    try { await client.query("ROLLBACK"); }
    catch { destroy = true; }
    // Keep deliberate application validation errors; all driver errors above are sanitized.
    throw error;
  } finally {
    client.removeListener("error", disconnected);
    client.release(destroy);
  }
}

export async function workspaceReady(): Promise<boolean> {
  try { await withWorkspace(false, async () => undefined); return true; }
  catch { return false; }
}
