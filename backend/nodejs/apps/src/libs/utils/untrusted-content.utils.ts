/**
 * Types a browser runs as a document (scripts, forms, links) when it opens
 * the response itself: HTML, SVG and the other XML documents, and scripts.
 * A stored file of one of these served inline at the app's origin would run
 * with the viewer's session (SEC-FS-04).
 */
const ACTIVE_TYPE =
  /^(?:text\/html|application\/xhtml\+xml|image\/svg\+xml|text\/xml|application\/xml|text\/xsl|application\/[\w.-]+\+xml|text\/(?:x-)?(?:java|ecma)script|application\/(?:x-)?(?:java|ecma)script|multipart\/x-mixed-replace)\b/i;

/** The CSP of untrusted bytes: a unique opaque origin that may load and run nothing. */
export const UNTRUSTED_CONTENT_CSP = "sandbox; default-src 'none'";

export function isActiveContentType(contentType: string | undefined): boolean {
  return contentType !== undefined && ACTIVE_TYPE.test(contentType.trim());
}

/** `disposition` as an attachment, its filename parameters kept. */
export function asAttachment(disposition: string | undefined): string {
  if (disposition === undefined || disposition.trim() === '') {
    return 'attachment';
  }
  const params = disposition.replace(/^\s*[\w-]+\s*(;|$)/, '').trim();
  return params === '' ? 'attachment' : `attachment; ${params}`;
}

/**
 * Headers for stored content someone else wrote (an uploaded file, an agent's
 * artifact): never sniffed into another type, and an active type is a sandboxed
 * download, never a page at the app's origin. The web app reads these bytes
 * with fetch, which none of this changes.
 */
export function untrustedContentHeaders(
  contentType: string | undefined,
  disposition?: string,
): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Content-Type-Options': 'nosniff',
  };
  if (isActiveContentType(contentType)) {
    headers['Content-Security-Policy'] = UNTRUSTED_CONTENT_CSP;
    headers['Content-Disposition'] = asAttachment(disposition);
  } else if (disposition !== undefined && disposition !== '') {
    headers['Content-Disposition'] = disposition;
  }
  return headers;
}

/** Sets the content type and `untrustedContentHeaders` through `set` (an Express or Node response's setter). */
export function setUntrustedContentHeaders(
  set: (name: string, value: string) => unknown,
  contentType: string | undefined,
  disposition?: string,
): void {
  if (contentType !== undefined && contentType !== '') {
    set('Content-Type', contentType);
  }
  for (const [name, value] of Object.entries(
    untrustedContentHeaders(contentType, disposition),
  )) {
    set(name, value);
  }
}
