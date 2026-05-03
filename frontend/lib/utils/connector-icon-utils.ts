/**
 * Resolve an API-provided connector iconPath to a local public path.
 *
 * The API returns paths like `/icons/connectors/slack.svg`.
 * Next.js serves `public/assets/…` at `/assets/…`, so the path works as-is.
 */
export function getConnectorIconPath(apiIconPath: string): string {
  return apiIconPath;
}
