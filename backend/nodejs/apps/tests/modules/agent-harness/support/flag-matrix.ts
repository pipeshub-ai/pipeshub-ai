import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/** `contracts/harness/flags.json` (F3), the one source of truth for harness flags. */

export const REPO_ROOT = join(__dirname, '../../../../../../..');

export interface MatrixFlag {
  name: string;
  kind: 'env' | 'labs' | 'config' | 'request';
  scope: string;
  default: boolean | string | null;
  defaultFollows?: string;
  killSwitchFor?: string;
  path?: string;
  readers: { node?: string[]; python?: string[]; shell?: string[] };
}

export interface MatrixNeed {
  flag?: string;
  feature?: string;
  condition?: string;
  on: unknown;
  whenNot: string;
  reason: string;
}

export interface MatrixFeature {
  name: string;
  requires: MatrixNeed[];
  whenOff: string;
  alwaysOn?: boolean;
}

export interface FlagMatrix {
  flags: MatrixFlag[];
  features: MatrixFeature[];
  settings: Array<{ name: string; kind: string; reason: string }>;
}

/**
 * An edition's overlay of the harness contracts (an extension point): a file
 * there named like a contract document adds entries to it.
 */
export const CONTRACT_OVERLAY = join(REPO_ROOT, 'contracts/harness/ee');

const ENTRY_KEYS = ['name', 'setting'] as const;

const entryKey = (entry: unknown): string => {
  if (typeof entry === 'object' && entry !== null) {
    for (const key of ENTRY_KEYS) {
      if (key in entry) return String((entry as Record<string, unknown>)[key]);
    }
  }
  return JSON.stringify(entry);
};

const isMap = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * `base` with the entries of `overlay/name`, when present: each list gains the
 * overlay's entries after its own, each map its keys. An overlay may only add;
 * the Python half is `with_overlay` in tests/cohesion/support.py.
 */
export function withOverlay<T extends object>(
  base: T,
  name: string,
  overlay: string = CONTRACT_OVERLAY,
): T {
  const path = join(overlay, name);
  if (!existsSync(path)) return base;
  const extra = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
  const own = base as Record<string, unknown>;
  const merged: Record<string, unknown> = { ...own };
  for (const [section, added] of Object.entries(extra)) {
    if (section === 'description') continue;
    if (!(section in own)) {
      throw new Error(`${path}: ${section} is not a section of ${name}`);
    }
    const current = own[section];
    if (Array.isArray(current) && Array.isArray(added)) {
      const known = new Set(current.map(entryKey));
      for (const entry of added) {
        if (known.has(entryKey(entry))) {
          throw new Error(`${path}: ${section} already has ${entryKey(entry)}`);
        }
      }
      merged[section] = [...current, ...added];
    } else if (isMap(current) && isMap(added)) {
      const repeated = Object.keys(added).filter((key) => key in current);
      if (repeated.length > 0) {
        throw new Error(`${path}: ${section} already has ${repeated.join(', ')}`);
      }
      merged[section] = { ...current, ...added };
    } else {
      throw new Error(`${path}: ${section} can only be added to as a list or a map`);
    }
  }
  return merged as T;
}

/** This repository's matrix alone: what FLAGS.md renders. */
export const baseFlagMatrix = (): FlagMatrix =>
  JSON.parse(
    readFileSync(join(REPO_ROOT, 'contracts/harness/flags.json'), 'utf8'),
  ) as FlagMatrix;

/** The matrix with an edition's overlay entries, when present. */
export const flagMatrix = (): FlagMatrix =>
  withOverlay(baseFlagMatrix(), 'flags.json');

export const matrixFlag = (name: string): MatrixFlag => {
  const found = flagMatrix().flags.find((flag) => flag.name === name);
  if (found === undefined) throw new Error(`${name} is not in flags.json`);
  return found;
};

/** The reason the matrix documents when `need` of `feature` is unmet. */
export const matrixReason = (feature: string, need: string): string => {
  const entry = flagMatrix()
    .features.find((f) => f.name === feature)
    ?.requires.find((n) => [n.flag, n.feature, n.condition].includes(need));
  if (entry === undefined) throw new Error(`${feature} does not need ${need}`);
  return entry.reason;
};
