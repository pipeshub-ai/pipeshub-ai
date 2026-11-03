/**
 * Pure parts of the storage fingerprint: the rule for "no writer may be mid-write" and the canonical value a reader compares.
 * The store reads and the object stat live in the controller; nothing here touches I/O.
 */
import { createHash } from 'crypto';

export const FINGERPRINT_VERSION = 1;

/**
 * The identity of a local file, synthetic because a file system has no ETag: `dev:ino:size:mtimeNs` hashed. Taken from a stat of
 * the path (`statObject`) or of an open descriptor (the download), so both compare the same thing.
 */
export function localEtagOf(st: {
  dev: bigint;
  ino: bigint;
  size: bigint;
  mtimeNs: bigint;
}): string {
  return createHash('sha1')
    .update(`${st.dev}:${st.ino}:${st.size}:${st.mtimeNs}`, 'utf8')
    .digest('hex');
}

/** What a stat of the stored object returns (the ETag has no quotes and no weak marker). */
export interface ObjectStat {
  size: number;
  etag: string;
  lastModified: string | null;
}

/** An empty list, or one whose every entry has expired, is settled; an entry that lapses exactly at `serverNow` is expired. */
export function isSettled(
  writers: readonly { until: number }[],
  serverNow: number,
): boolean {
  return !writers.some((w) => w.until > serverNow);
}

/**
 * One ETag form for every vendor: the outer quotes and a weak marker are dropped. S3 and Azure return a quoted tag, the local
 * provider a bare digest. The consumer adds exactly one pair of quotes when it builds `If-Match`.
 */
export function normalizeEtag(raw: string | null | undefined): string {
  let t = (raw ?? '').trim();
  if (t.startsWith('W/')) t = t.slice(2);
  if (t.length >= 2 && t.startsWith('"') && t.endsWith('"')) t = t.slice(1, -1);
  return t;
}

/** The deployment epoch: bumped by an operator after a restore or a store migration, so every stored value stops matching. */
export function fingerprintEpoch(): string {
  const raw = (process.env.STORAGE_FINGERPRINT_EPOCH ?? '').trim();
  return raw === '' ? '0' : raw;
}

export interface FingerprintInput {
  epoch: string;
  writeSeq: number;
  mutationCount: number;
  versionCount: number;
  /** The object's size when verified, else the row's `sizeInBytes`. */
  size: number | null | undefined;
  etag: string | null | undefined;
}

/**
 * `hex(sha256("v1|epoch|writeSeq|mutationCount|versionCount|size|etag"))`, first 32 characters. Comparable only between verified
 * responses. `lastModified` is not part of it.
 */
export function fingerprintValue(i: FingerprintInput): string {
  const text = [
    `v${String(FINGERPRINT_VERSION)}`,
    i.epoch,
    String(i.writeSeq),
    String(i.mutationCount),
    String(i.versionCount),
    i.size === null || i.size === undefined ? '' : String(i.size),
    i.etag ?? '',
  ].join('|');
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 32);
}
