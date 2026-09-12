const routes: Record<string, RegExp[]> = {
  GET: [/^workspace$/, /^companies\/search$/, /^leads\/[a-zA-Z0-9_-]+\/detail$/, /^export\/(leads\.csv|backup\.json)$/],
  POST: [/^leads$/, /^leads\/import$/, /^campaigns$/, /^drafts\/[a-zA-Z0-9_-]+\/(prepare|mark-sent)$/],
  PUT: [/^profile$/, /^leads\/[a-zA-Z0-9_-]+$/, /^drafts\/[a-zA-Z0-9_-]+$/],
};

export function allowedProxyRoute(method: string, path: string[]): boolean {
  return Boolean(routes[method]?.some((route) => route.test(path.join("/"))));
}

export function backendOrigin(value: string | undefined, production: boolean): string | null {
  if (!value && production) return null;
  try {
    const parsed = new URL(value || "http://127.0.0.1:8001");
    if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash || !["http:", "https:"].includes(parsed.protocol)) return null;
    if (production && parsed.protocol !== "https:") return null;
    return parsed.origin;
  } catch { return null; }
}
