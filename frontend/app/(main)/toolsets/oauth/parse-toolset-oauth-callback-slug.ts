const CALLBACK_PREFIX = '/toolsets/oauth/callback';

/**
 * Reads the toolset segment from the OAuth return path, e.g.
 * `/toolsets/oauth/callback/jira` → `jira`, `/toolsets/oauth/callback/jira/` → `jira`.
 * For `/toolsets/oauth/callback` or `/toolsets/oauth/callback/` returns `undefined`.
 */
export function parseToolsetOAuthCallbackSlug(pathname: string): string | undefined {
  const path = (pathname || '/').replace(/\/+$/, '') || '/';
  if (!path.startsWith(CALLBACK_PREFIX)) return undefined;
  const tail = path.slice(CALLBACK_PREFIX.length).replace(/^\/+/, '');
  if (!tail) return undefined;
  const [rawSlug] = tail.split('/');
  if (!rawSlug) return undefined;
  try {
    const slug = decodeURIComponent(rawSlug).trim();
    return slug || undefined;
  } catch {
    return rawSlug.trim() || undefined;
  }
}
