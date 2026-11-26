import { createHash } from 'node:crypto';
import { SealContext } from './data-protection';

/**
 * Worker content too large to inline in a journal event (tool results, model
 * responses), sealed by Node with the org's data key and stored as opaque
 * bytes (REPORT 6.16, DP-1). Workers hold refs, never keys.
 */
export const CONTENT_REF_PREFIX = 'sealed:';

/** The whole body is buffered to seal it (AES-GCM authenticates the whole). */
export const CONTENT_MAX_BYTES = 8 * 1024 * 1024;

export interface ContentRef {
  /** The run that wrote it; its session's runs may read it. */
  writerRunId: string;
  /** The payload store's ref of the sealed bytes. */
  storeRef: string;
}

/** Run ids never contain `/` (bounded ids), so the first one splits. */
export const formatContentRef = (ref: ContentRef): string =>
  `${CONTENT_REF_PREFIX}${ref.writerRunId}/${ref.storeRef}`;

export function parseContentRef(raw: string): ContentRef | null {
  if (!raw.startsWith(CONTENT_REF_PREFIX)) return null;
  const body = raw.slice(CONTENT_REF_PREFIX.length);
  const split = body.indexOf('/');
  if (split <= 0 || split === body.length - 1) return null;
  return {
    writerRunId: body.slice(0, split),
    storeRef: body.slice(split + 1),
  };
}

/** Content Node carries in its own ref (`inline:` + padded base64url), as the worker's store does. */
export const INLINE_CONTENT_REF_PREFIX = 'inline:';

export function inlineContentRef(bytes: Buffer): {
  ref: string;
  hash: string;
  size: number;
} {
  const text = bytes.toString('base64url');
  return {
    ref: `${INLINE_CONTENT_REF_PREFIX}${text}${'='.repeat((4 - (text.length % 4)) % 4)}`,
    hash: `sha256:${createHash('sha256').update(bytes).digest('hex')}`,
    size: bytes.length,
  };
}

/** Org, key and purpose are bound by the cipher; this binds the writer. */
export const contentSealContext = (
  streamId: string,
  writerRunId: string,
): SealContext => ({ streamId, runId: writerRunId });

export type ContentRefusal =
  | 'malformed_ref'
  | 'foreign_run'
  | 'not_owner'
  | 'too_large'
  | 'unsupported_media'
  | 'missing'
  | 'corrupt'
  | 'shredded'
  | 'unsupported_version'
  | 'unwrap_failed'
  | 'store_unavailable';
