// Query params that mark a storage-provider signed URL: Azure SAS (sig),
// S3 SigV2/SigV4 and GCS V2 (signature, x-amz-signature), GCS V4 (x-goog-signature).
// Kept in step with SIGNATURE_QUERY_PARAMS in backend/python/app/utils/url_redaction.py.
export const SIGNED_URL_QUERY_PARAMS = [
  'sig',
  'signature',
  'x-amz-signature',
  'x-goog-signature',
] as const;

const SIGNED_URL_PARAM_RE = new RegExp(
  `[?&#](?:${SIGNED_URL_QUERY_PARAMS.join('|')})=`,
  'i',
);

// Optional `::marker` prefix or `!` image bang, `[label](url)`, optional
// `{...}` artifact suffix. Labels and URLs cannot span lines; the bounds keep
// matching linear on pathological input (a bare signed URL longer than the
// bound is still caught by BARE_URL_RE).
const MARKDOWN_LINK_RE =
  /(?:::[a-z_]+|!)?\[[^\]\n]{0,1000}\]\(([^)\s\n]{0,4096})(?:\s+"[^"\n]{0,1000}")?\)(?:\{[^}\n]{0,1000}\})?/g;

const BARE_URL_RE = /https?:\/\/[^\s<>"'`)\]]+/gi;

export const SIGNED_URL_PLACEHOLDER = '[link removed]';

export const hasSignedUrlQuery = (url: string): boolean =>
  SIGNED_URL_PARAM_RE.test(url);

/** Removes every storage-signed URL from assistant text so a persisted answer
 * never holds an expiring credential: markdown links and `::artifact` /
 * `::download_conversation_task` markers are dropped, and any remaining bare
 * URL is replaced with a neutral placeholder. Unsigned URLs, citations and
 * `record:` markers are untouched. */
export const stripSignedUrlLinks = (content: string): string =>
  content
    .replace(MARKDOWN_LINK_RE, (match, url: string) =>
      hasSignedUrlQuery(url) ? '' : match,
    )
    .replace(BARE_URL_RE, (url) =>
      hasSignedUrlQuery(url) ? SIGNED_URL_PLACEHOLDER : url,
    );
