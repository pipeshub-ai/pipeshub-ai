import type { PlatformFeatureFlagDef } from '../constants/constants';

/**
 * Labs records only the flags an admin changed (UX1). A flag with no stored
 * override follows its release default, so a default that changes in a later
 * release reaches every org that never chose otherwise.
 *
 * Settings saved before this release stored every flag the page showed, so a
 * stored value cannot be told apart from a choice. They are read without a
 * write: a value equal to today's default is no override (the effective value
 * is the same either way); any other value is one (kept, never silently
 * changed). Where the release default itself changed (`previousDefault`), a
 * stored previous default is ambiguous: it is kept, and marked
 * `notices.durableAgentDefault: 'pending'` for the durable harness.
 *
 * The notice is the admin's one answer about the agent harness (BETA): while it
 * is unanswered (absent or pending) and the harness is off for the org, the
 * workspace tells admins it can be turned on in Labs. Turning the flag on or
 * off in Labs, or dismissing the notice, answers it.
 */
export const FEATURE_FLAGS_VERSION = 2;
export const DURABLE_AGENT_FLAG = 'ENABLE_AGENT_HARNESS';
/** The Labs kill switch before CTL-5 made it the operator's env setting. */
const RETIRED_KILL_SWITCH = 'AGENT_HARNESS_KILL_SWITCH';

export type DurableDefaultNotice = 'pending' | 'adopted' | 'kept' | 'dismissed';
export type DurableDefaultChoice = Exclude<DurableDefaultNotice, 'pending'>;

export interface PlatformNotices {
  durableAgentDefault?: DurableDefaultNotice;
}

export interface StoredFlagState {
  overrides: Record<string, boolean>;
  notices: PlatformNotices;
  /** The document was saved before overrides were recorded (read only, never rewritten on read). */
  legacy: boolean;
}

type Registry = readonly PlatformFeatureFlagDef[];

const booleans = (value: unknown): Record<string, boolean> => {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter(
      (entry): entry is [string, boolean] => typeof entry[1] === 'boolean',
    ),
  );
};

const NOTICE_STATES: ReadonlySet<string> = new Set([
  'pending',
  'adopted',
  'kept',
  'dismissed',
]);

const storedNotices = (value: unknown): PlatformNotices => {
  const notice =
    value !== null && typeof value === 'object'
      ? (value as { durableAgentDefault?: unknown }).durableAgentDefault
      : undefined;
  return typeof notice === 'string' && NOTICE_STATES.has(notice)
    ? { durableAgentDefault: notice as DurableDefaultNotice }
    : {};
};

export const releaseDefault = (
  key: string,
  registry: Registry,
): boolean | undefined => {
  const def = registry.find((flag) => flag.key === key);
  return def === undefined ? undefined : !!def.defaultEnabled;
};

/**
 * What a stored settings document says the admin chose, read without changing it. Only flags this release lists
 * are read: a retired flag's stored key is ignored, never shown, and gone from the next save (CTL-5). A retired Labs
 * kill switch that was on reads as the harness off for the org, so retiring the switch never turns it back on.
 */
export function readFlagState(
  stored: {
    featureFlagsVersion?: unknown;
    featureFlags?: unknown;
    notices?: unknown;
  },
  registry: Registry,
): StoredFlagState {
  const state = readListedFlagState(stored, registry);
  if (booleans(stored.featureFlags)[RETIRED_KILL_SWITCH] === true) {
    state.overrides[DURABLE_AGENT_FLAG] = false;
  }
  return state;
}

function readListedFlagState(
  stored: {
    featureFlagsVersion?: unknown;
    featureFlags?: unknown;
    notices?: unknown;
  },
  registry: Registry,
): StoredFlagState {
  const flags = Object.fromEntries(
    Object.entries(booleans(stored.featureFlags)).filter(
      ([key]) => releaseDefault(key, registry) !== undefined,
    ),
  );
  if (stored.featureFlagsVersion === FEATURE_FLAGS_VERSION) {
    return {
      overrides: flags,
      notices: storedNotices(stored.notices),
      legacy: false,
    };
  }
  const overrides: Record<string, boolean> = {};
  let ambiguousDurable = false;
  for (const [key, value] of Object.entries(flags)) {
    const def = registry.find((flag) => flag.key === key);
    if (def !== undefined && value !== !!def.defaultEnabled)
      overrides[key] = value;
    if (
      key === DURABLE_AGENT_FLAG &&
      def?.previousDefault !== undefined &&
      value === def.previousDefault &&
      value !== !!def.defaultEnabled
    ) {
      ambiguousDurable = true;
    }
  }
  return {
    overrides,
    notices: ambiguousDurable ? { durableAgentDefault: 'pending' } : {},
    legacy: true,
  };
}

/** Every listed flag's value: its override, else its release default. */
export function effectiveFlags(
  overrides: Record<string, boolean>,
  registry: Registry,
): Record<string, boolean> {
  const flags: Record<string, boolean> = { ...overrides };
  for (const def of registry) {
    if (typeof flags[def.key] !== 'boolean')
      flags[def.key] = !!def.defaultEnabled;
  }
  return flags;
}

export interface FlagChanges {
  /** Flags the admin set: one at its release default drops its override; any other value is recorded. */
  featureFlags?: Record<string, boolean>;
  notices?: { durableAgentDefault?: DurableDefaultChoice };
}

/** The flag state after an admin's save; the document is always written in the override form. */
export function applyFlagChanges(
  current: StoredFlagState,
  changes: FlagChanges,
  registry: Registry,
): StoredFlagState {
  const overrides = new Map(Object.entries(current.overrides));
  const set = (key: string, value: boolean, known: boolean): void => {
    if (known === value) overrides.delete(key);
    else overrides.set(key, value);
  };
  let notice = current.notices.durableAgentDefault;
  const unanswered = (): boolean =>
    notice === undefined || notice === 'pending';
  for (const [key, value] of Object.entries(changes.featureFlags ?? {})) {
    const known = releaseDefault(key, registry);
    if (known === undefined) continue;
    set(key, value, known);
    // Setting the flag on the Labs page is itself the choice the notice asks for.
    if (key === DURABLE_AGENT_FLAG && unanswered()) {
      notice = value ? 'adopted' : 'kept';
    }
  }
  const choice = changes.notices?.durableAgentDefault;
  const durableDefault = releaseDefault(DURABLE_AGENT_FLAG, registry);
  if (choice !== undefined && unanswered()) {
    if (durableDefault !== undefined && choice !== 'dismissed') {
      set(DURABLE_AGENT_FLAG, choice === 'adopted', durableDefault);
    }
    notice = choice;
  }
  return {
    overrides: Object.fromEntries(overrides),
    notices: notice !== undefined ? { durableAgentDefault: notice } : {},
    legacy: false,
  };
}
