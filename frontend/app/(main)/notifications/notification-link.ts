/** App-relative paths from the API may omit a leading slash; Next.js needs one. */
export function notificationHref(redirectLink: string | undefined): string | null {
  if (!redirectLink) return null;
  const trimmed = redirectLink.trim();
  if (!trimmed) return null;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}
