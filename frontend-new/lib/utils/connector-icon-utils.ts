/**
 * Utilities for resolving connector icon paths.
 *
 * The API returns icon paths prefixed with `/assets` (e.g. `/assets/icons/connectors/slack.svg`),
 * which maps to the `public/` directory in Next.js.
 * This helper strips that prefix so the path works as a direct `<img src>` or `next/image` src.
 */

/**
 * Convert an API-provided connector iconPath to a local public path.
 *
 * @example
 * getConnectorIconPath('/assets/icons/connectors/slack.svg')
 * // → '/icons/connectors/slack.svg'
 */
export function getConnectorIconPath(apiIconPath: string): string {
  if (apiIconPath.startsWith('/assets')) {
    return apiIconPath.replace('/assets', '');
  }
  return apiIconPath;
}
