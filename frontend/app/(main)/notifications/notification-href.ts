// Not in the row component: code with no UI tests a notice's link here without loading the row and all it imports.

/**
 * An absolute http(s) URL, or a protocol-relative one (`//host`, and `/\host`, which browsers
 * read the same way): it leaves the app, so it opens in a new tab and is never handled in-app.
 */
export function isExternalNotificationHref(href: string): boolean {
  return /^(?:https?:)?[/\\]{2}/i.test(href);
}
