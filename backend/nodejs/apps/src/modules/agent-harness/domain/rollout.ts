import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';

/** Admission pins harness v2 (typed journal, pinned RunSpec); v1 runs are failed on claim. */
export const HARNESS_MAJORS = [1, 2] as const;
export type HarnessMajor = (typeof HARNESS_MAJORS)[number];
export const CURRENT_HARNESS_MAJOR: HarnessMajor = 2;

/** Runs admitted before majors were recorded were all durable v1. */
export function pinnedHarnessMajor(value: unknown): HarnessMajor {
  return (HARNESS_MAJORS as readonly unknown[]).includes(value)
    ? (value as HarnessMajor)
    : 1;
}

export interface HarnessRolloutSettings {
  /** Transactions, relay and worker are all available in this deployment. */
  infrastructureReady: boolean;
  /** Platform-level: routes every new admission to legacy. */
  killSwitch: boolean;
  orgEnabled: boolean;
  /** Lower-case model families allowed on the harness; null allows every family. */
  modelFamilies: readonly string[] | null;
  /**
   * The assistant modes the org runs durably (CPD-10's
   * `rollout.durable.assistantModes`); absent: the release's. `agent` is
   * added only by the org's Labs switch, and only a turn that asks uses it.
   */
  assistantModes?: readonly string[];
}

export type HarnessRolloutReason =
  | 'enabled'
  | 'infrastructure_unavailable'
  | 'kill_switch'
  | 'org_disabled'
  | 'model_family_not_allowed'
  | 'model_family_unknown'
  | 'chat_mode_not_supported'
  /** The conversation belongs to a project, whose knowledge only today's loop serves (UX3). */
  | 'project_scope'
  /** No worker was seen lately and the session has no durable turns to keep together (UX3). */
  | 'no_live_worker'
  /** The person's chat switch asked for the classic path, for a conversation with no durable turns (CTL-7). */
  | 'user_chose_classic'
  /** The sender is in none of the groups the org lets use the harness (G-41, the access section). */
  | 'group_not_allowed';

export interface HarnessRolloutDecision {
  useHarness: boolean;
  harnessMajor: HarnessMajor;
  reason: HarnessRolloutReason;
}

export const normalizeModelFamily = (value: string): string =>
  value.trim().toLowerCase();

/**
 * Chat modes the durable loop runs as legacy does: the react loop kind,
 * `deep` (its sub-agents are durable child runs, K-L), plus `auto`, whose
 * tier the run's goal step picks. planExecute keeps the legacy path: it never
 * delegates, and its phase loop has no durable director yet. Fail closed: any
 * other value stays legacy, where an unknown mode means `auto` anyway. The
 * release layer's `rollout.durable.modes`, which a deployment or an org
 * narrows (CPD-10); `contracts/harness/vectors/durable-chat-modes.json` holds
 * it to Python's MODE_CATALOG.
 */
export const DURABLE_CHAT_MODES: readonly string[] =
  RELEASE_DEFAULTS.rollout.durable.modes;

/**
 * Deep mode delegates by design: an agent whose definition sets no
 * `maxChildren` may admit this many children per deep run (the definition's
 * own maximum). Every other mode admits none unless the definition says so.
 */
export const DEEP_MAX_CHILDREN = RELEASE_DEFAULTS.children.maxPerRun;

export const modeMaxChildren = (chatMode: string): number =>
  chatMode.trim().toLowerCase() === 'deep' ? DEEP_MAX_CHILDREN : 0;

/**
 * The built-in assistant's modes that legacy serves from `/chat/stream`
 * (`chat_modes/policy.py`'s canonical names). A mode naming `agent` is
 * Universal Agent Mode, which legacy runs on the agent route
 * (`agentIdPlaceholder`, `parseChatMode`), and any other value falls back in
 * ways the pin does not record: both stay legacy. The release layer's
 * `rollout.durable.assistantModes` (the same vector's `assistantChatModes`).
 */
export const DURABLE_ASSISTANT_CHAT_MODES: readonly string[] =
  RELEASE_DEFAULTS.rollout.durable.assistantModes;

/** The system assistant definition (ASSIST1): legacy's name for Universal Agent Mode. */
export const ASSISTANT_AGENT_DEFINITION_KEY = 'agentIdPlaceholder';

const ASSISTANT_AGENT_MODE = 'agent';

/** The org's durable assistant modes: the release's, plus Agent mode where the org turned it on. */
export const assistantModesWith = (
  agentModeEnabled: boolean,
): readonly string[] =>
  agentModeEnabled
    ? [...DURABLE_ASSISTANT_CHAT_MODES, ASSISTANT_AGENT_MODE]
    : DURABLE_ASSISTANT_CHAT_MODES;

/**
 * The loop mode legacy runs an assistant mode naming `agent` in
 * (`parseChatMode`): `agent` is `quick`, `agent:<mode>` that mode; null for
 * any other mode.
 */
export function assistantAgentLoopMode(chatMode: string): string | null {
  const mode = chatMode.trim();
  if (!mode.toLowerCase().includes(ASSISTANT_AGENT_MODE)) return null;
  return mode.split(':')[1] || 'quick';
}

export const isDurableChatMode = (
  chatMode: string,
  assistant = false,
): boolean =>
  (assistant ? DURABLE_ASSISTANT_CHAT_MODES : DURABLE_CHAT_MODES).includes(
    chatMode.trim().toLowerCase(),
  );

/** The decision for a turn the conversation itself keeps on today's loop. */
export const legacyFor = (
  reason: Extract<
    HarnessRolloutReason,
    'project_scope' | 'no_live_worker' | 'user_chose_classic'
  >,
): HarnessRolloutDecision => decision(false, reason);

const decision = (
  useHarness: boolean,
  reason: HarnessRolloutReason,
): HarnessRolloutDecision => ({
  useHarness,
  harnessMajor: CURRENT_HARNESS_MAJOR,
  reason,
});

/**
 * Whether the harness is on for the org (ADR-OPS-08): its Labs switch, read where the server switch is on. Unlike
 * `decideOrgRollout`, readiness, the kill switch, operator stops and model families do not turn it off: they stop new
 * admissions only, while what the org sees and what its deletes and agent chats do stay as they are.
 */
export const orgHarnessOnFrom = (
  settings: Pick<HarnessRolloutSettings, 'orgEnabled'>,
): boolean => settings.orgEnabled;

/** Whether any admission for the org could use the harness (model not yet known). */
export function decideOrgRollout(
  settings: HarnessRolloutSettings,
): HarnessRolloutDecision {
  if (!settings.infrastructureReady) {
    return decision(false, 'infrastructure_unavailable');
  }
  if (settings.killSwitch) return decision(false, 'kill_switch');
  if (!settings.orgEnabled) return decision(false, 'org_disabled');
  if (settings.modelFamilies !== null && settings.modelFamilies.length === 0) {
    return decision(false, 'model_family_not_allowed');
  }
  return decision(true, 'enabled');
}

export interface AssistantTurnRollout {
  /** The org's durable assistant modes (`HarnessRolloutSettings.assistantModes`). */
  assistantModes?: readonly string[];
  /** The turn asked for the durable harness (`harness: "durable"`). */
  durableRequested?: boolean;
}

/**
 * The assistant's Agent mode (Universal Agent Mode) runs durably only when the
 * turn asked, the org allows it, and its loop mode is one the durable loop runs.
 */
const durableAssistantAgent = (
  chatMode: string,
  turn: AssistantTurnRollout,
): boolean => {
  const loop = assistantAgentLoopMode(chatMode);
  return (
    loop !== null &&
    turn.durableRequested === true &&
    (turn.assistantModes ?? []).includes(ASSISTANT_AGENT_MODE) &&
    isDurableChatMode(loop)
  );
};

/**
 * Narrows an allowed decision to the chat modes the durable loop runs: an
 * agent's loop modes, or the built-in assistant's chat modes.
 */
export function decideChatModeRollout(
  org: HarnessRolloutDecision,
  chatMode: string,
  assistant = false,
  turn: AssistantTurnRollout = {},
): HarnessRolloutDecision {
  if (!org.useHarness || isDurableChatMode(chatMode, assistant)) return org;
  if (assistant && durableAssistantAgent(chatMode, turn)) return org;
  return decision(false, 'chat_mode_not_supported');
}

/** The decision for one admission; `modelFamily` null means it could not be resolved. */
export function decideAdmissionRollout(
  settings: HarnessRolloutSettings,
  modelFamily: string | null,
): HarnessRolloutDecision {
  const org = decideOrgRollout(settings);
  if (!org.useHarness || settings.modelFamilies === null) return org;
  if (modelFamily === null) return decision(false, 'model_family_unknown');
  const allowed = settings.modelFamilies.map(normalizeModelFamily);
  return allowed.includes(normalizeModelFamily(modelFamily))
    ? org
    : decision(false, 'model_family_not_allowed');
}
