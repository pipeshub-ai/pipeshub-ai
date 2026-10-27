import { randomUUID } from 'crypto';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../config/config';
import { configPaths } from '../paths/paths';

/**
 * The stored-write protocol's rollout rules for the platform settings save.
 *
 * `artifactReadGeneration` identifies the current shadow/read activation: a run minted under
 * another generation is never served. It is set by the server (never from the request body) when
 * a flag turns on, kept while one stays on, and null when both are off.
 *
 * Turning a flag on is refused (409) while an indexing member that does not write the stored
 * record by compare-and-set may be running, or while the indexing leader's membership check
 * cannot be seen: the leader publishes one entry per holder under MEMBERSHIP_DIRECTORY.
 */
export const GENERATION_KEY = 'artifactReadGeneration';
export const MEMBERSHIP_DIRECTORY =
  '/services/platform/artifactRollout/membership/';
/** An entry older than this (ms, on the checker's clock) is stale: three timer periods of 30 s. */
export const MEMBERSHIP_STALE_MS = 90_000;

const READ = 'ENABLE_ARTIFACT_READ';
const SHADOW = 'ENABLE_ARTIFACT_READ_SHADOW';

type Flags = Record<string, unknown> | undefined | null;

function flagOn(flags: Flags, name: string): boolean {
  if (!flags || typeof flags !== 'object') return false;
  return Object.entries(flags).some(
    ([key, value]) => key.toUpperCase() === name && Boolean(value),
  );
}

export function artifactFlagsOn(flags: Flags): boolean {
  return flagOn(flags, READ) || flagOn(flags, SHADOW);
}

/**
 * Whether a save needs the membership check: it turns read on, or turns shadow on from both off.
 * Read to shadow, saves that leave the flags as they are, and turning flags off are never refused.
 */
export function savePutsArtifactReadsAtRisk(
  before: Flags,
  after: Flags,
): boolean {
  if (flagOn(after, READ) && !flagOn(before, READ)) return true;
  return flagOn(after, SHADOW) && !artifactFlagsOn(before);
}

/** The generation to store: new on off to on, kept while on (re-stamped if missing), null when off. */
export function nextGeneration(
  storedGeneration: unknown,
  before: Flags,
  after: Flags,
): string | null {
  if (!artifactFlagsOn(after)) return null;
  const kept =
    typeof storedGeneration === 'string' && storedGeneration
      ? storedGeneration
      : null;
  if (artifactFlagsOn(before) && kept) return kept;
  return randomUUID();
}

export interface MembershipVerdict {
  ok: boolean;
  untagged: string[];
  reason?: 'no_entry' | 'stale' | 'untagged';
}

/**
 * The union over fresh entries: untagged if any fresh entry lists an untagged member, stale
 * when none is fresh. An ex-leader's clean entry cannot hide the new leader's finding.
 */
export function evaluateMembership(
  entries: unknown[],
  now: number,
  staleMs: number = MEMBERSHIP_STALE_MS,
): MembershipVerdict {
  const untagged = new Set<string>();
  let fresh = 0;
  for (const entry of entries) {
    const value = entry as { checkedAt?: unknown; untagged?: unknown } | null;
    if (!value || typeof value.checkedAt !== 'number') continue;
    if (now - value.checkedAt > staleMs) continue;
    fresh += 1;
    if (Array.isArray(value.untagged)) {
      for (const name of value.untagged) untagged.add(String(name));
    }
  }
  if (fresh === 0) return { ok: false, untagged: [], reason: 'stale' };
  if (untagged.size > 0)
    return { ok: false, untagged: [...untagged], reason: 'untagged' };
  return { ok: true, untagged: [] };
}

function parseEntry(raw: unknown): unknown {
  // Redis keeps a dict as a JSON string of JSON, so a string may need parsing twice.
  let value = raw;
  for (let i = 0; i < 2 && typeof value === 'string'; i += 1) {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  return value;
}

export async function readMembership(
  keyValueStoreService: KeyValueStoreService,
  now: number = Date.now(),
): Promise<MembershipVerdict> {
  let keys: string[];
  try {
    keys = await keyValueStoreService.listKeysInDirectory(MEMBERSHIP_DIRECTORY);
  } catch {
    return { ok: false, untagged: [], reason: 'stale' };
  }
  const entries: unknown[] = [];
  for (const key of keys) {
    try {
      entries.push(parseEntry(await keyValueStoreService.get<unknown>(key)));
    } catch {
      // An unreadable entry counts as no entry.
    }
  }
  return evaluateMembership(entries, now);
}

/** The stored blob as saved (decrypted), including fields the typed reader drops. */
export async function readStoredPlatformBlob(
  keyValueStoreService: KeyValueStoreService,
): Promise<Record<string, unknown>> {
  const encrypted = await keyValueStoreService.get<string>(
    configPaths.platform.settings,
  );
  if (!encrypted) return {};
  const cfg = loadConfigurationManagerConfig();
  try {
    const parsed: unknown = JSON.parse(
      EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).decrypt(
        encrypted,
      ),
    );
    return parsed !== null && typeof parsed === 'object'
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}
