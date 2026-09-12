import "server-only";
import { withWorkspace } from "./workspace-db.ts";
import { loginClientKey, nextLoginRateLimit } from "./login-rate-limit-core.ts";

/** Database errors propagate to the login handler, which must fail closed. */
export async function takeDistributedLoginAttempt(request: Request, secret: string): Promise<boolean> {
  const clientKey = loginClientKey(request, secret, process.env);
  return withWorkspace(true, async (db) => {
    // withWorkspace takes the cross-instance advisory lock before this read.
    const row = (await db.query("SELECT state FROM login_rate_limit WHERE id=1")).rows[0];
    if (!row) throw new Error("Login rate limit is unavailable.");
    const { allowed, state } = nextLoginRateLimit(row.state, clientKey);
    const result = await db.query("UPDATE login_rate_limit SET state=$1::jsonb WHERE id=1", [JSON.stringify(state)]);
    if (result.rowCount !== 1) throw new Error("Login rate limit is unavailable.");
    return allowed;
  });
}
