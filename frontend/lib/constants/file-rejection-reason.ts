/**
 * Stable rejection codes from the file-processor middleware / KB upload API.
 * Must stay in sync with backend `FileRejectionReason` in fp.constant.ts.
 */
export const FileRejectionReason = {
  EXCEEDS_SIZE_LIMIT: 'EXCEEDS_SIZE_LIMIT',
  UNSUPPORTED_TYPE: 'UNSUPPORTED_TYPE',
  DUPLICATE_NAME: 'DUPLICATE_NAME',
} as const;

export type FileRejectionReason =
  (typeof FileRejectionReason)[keyof typeof FileRejectionReason];

const REJECTION_REASON_VALUES = new Set<string>(Object.values(FileRejectionReason));

export function parseFileRejectionReason(
  value: unknown,
): FileRejectionReason | undefined {
  if (typeof value === 'string' && REJECTION_REASON_VALUES.has(value)) {
    return value as FileRejectionReason;
  }
  return undefined;
}

export type UploadFailureKind =
  | 'size_limit'
  | 'unsupported_type'
  | 'duplicate_name'
  | 'other';

/**
 * Single source of truth mapping a stable reason code to its display kind, in
 * priority order (most specific first). To support a NEW rejection reason, add
 * one row here (and its i18n/presentation in upload-failure-feedback.ts) — no
 * switch/if-chain to touch. Anything without a row falls through to `'other'`,
 * so unrecognised or non-rejection errors (rate limit, 5xx, transport) are still
 * surfaced generically rather than dropped.
 */
const REASON_KIND_TABLE: ReadonlyArray<{
  reason: FileRejectionReason;
  kind: UploadFailureKind;
}> = [
  { reason: FileRejectionReason.EXCEEDS_SIZE_LIMIT, kind: 'size_limit' },
  { reason: FileRejectionReason.UNSUPPORTED_TYPE, kind: 'unsupported_type' },
  { reason: FileRejectionReason.DUPLICATE_NAME, kind: 'duplicate_name' },
];

const REASON_TO_KIND: ReadonlyMap<FileRejectionReason, UploadFailureKind> =
  new Map(REASON_KIND_TABLE.map((e) => [e.reason, e.kind]));

/** Failure kinds ordered most-specific → least, with `'other'` last. */
export const UPLOAD_FAILURE_KIND_ORDER: readonly UploadFailureKind[] = [
  ...REASON_KIND_TABLE.map((e) => e.kind),
  'other',
];

export function uploadFailureKindFromReason(
  reason: FileRejectionReason,
): UploadFailureKind {
  return REASON_TO_KIND.get(reason) ?? 'other';
}

/** Pick the most specific failure kind when a file has multiple reason codes. */
export function primaryUploadFailureKind(
  reasons: FileRejectionReason[],
): UploadFailureKind {
  for (const { reason, kind } of REASON_KIND_TABLE) {
    if (reasons.includes(reason)) return kind;
  }
  return 'other';
}
