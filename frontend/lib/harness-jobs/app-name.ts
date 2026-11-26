/** "GOOGLE_DRIVE" reads "Google Drive"; a web citation reads "Web". */
export function appName(connector: string | undefined): string {
  const raw = (connector ?? '').trim();
  if (!raw) return '';
  return raw
    .toLowerCase()
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}
