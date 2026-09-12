export type WorkspaceBackend = "proxy" | "postgres";

/** An explicit but unsupported mode must fail closed, never select another data store. */
export function workspaceBackend(env: Record<string, string | undefined>): WorkspaceBackend | null {
  const value = env.WORKSPACE_BACKEND;
  return !value || value === "proxy" ? "proxy" : value === "postgres" ? "postgres" : null;
}
