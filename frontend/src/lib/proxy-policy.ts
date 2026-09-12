const routes: Record<string, RegExp[]> = {
  GET: [/^workspace$/, /^companies\/search$/, /^leads\/[a-zA-Z0-9_-]+\/detail$/, /^export\/(leads\.csv|backup\.json)$/],
  POST: [/^leads$/, /^leads\/import$/, /^campaigns$/, /^drafts\/[a-zA-Z0-9_-]+\/(prepare|mark-sent)$/],
  PUT: [/^profile$/, /^leads\/[a-zA-Z0-9_-]+$/, /^drafts\/[a-zA-Z0-9_-]+$/],
};

export function allowedProxyRoute(method: string, path: string[]): boolean {
  return Boolean(routes[method]?.some((route) => route.test(path.join("/"))));
}

export const INTERNAL_BACKEND_ORIGIN = "http://127.0.0.1:8001";

export function backendOrigin(value: string | undefined, production: boolean, internal = false): string | null {
  if (!value && production) return null;
  try {
    const parsed = new URL(value || "http://127.0.0.1:8001");
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash || !["http:", "https:"].includes(parsed.protocol)) return null;
    if (production && parsed.protocol !== "https:" && !(internal && value === INTERNAL_BACKEND_ORIGIN)) return null;
    return parsed.origin;
  } catch { return null; }
}

export function backendHostHeader(backend: string, publicOrigin: string | null): Record<string, string> {
  if (backend !== INTERNAL_BACKEND_ORIGIN || !publicOrigin) return {};
  // publicOrigin has already passed the authentication configuration validator.
  return { Host: new URL(publicOrigin).host };
}
