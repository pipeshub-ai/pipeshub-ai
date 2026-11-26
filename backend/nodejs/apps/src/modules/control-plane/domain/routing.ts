import type { ResolvedRunConfig } from '../contracts/generated';
import type { EffectiveRunConfig } from './policy-alias';

/** A pin's `routing`: the path one turn takes, and why when it is today's loop. */
export type Routing = ResolvedRunConfig['routing'];

/** Why a turn stays on today's loop, in the order `routingOf` decides. */
export const LEGACY_REASONS = [
  'infrastructure_unavailable',
  'kill_switch',
  'org_disabled',
  'model_family_not_allowed',
  'chat_mode_not_supported',
  'model_family_unknown',
  'project_scope',
  'no_live_worker',
  'user_chose_classic',
] as const;
export type LegacyReason = (typeof LEGACY_REASONS)[number];

/**
 * What routing reads that is not policy, each decided by the server and
 * never by the client: the conversation's record, this process, the workers
 * seen lately.
 */
export interface RoutingFacts {
  /** Transactions, relay and worker are available in this deployment. */
  readonly infrastructureReady: boolean;
  readonly workerLive: boolean;
  /** A schedule's firing: no person waits on it, so it waits for a worker. */
  readonly scheduled: boolean;
  /** The conversation's record; null for a new conversation, or one that could not be read. */
  readonly placement: {
    /** It has a durable turn: its later turns stay with it. */
    readonly durable: boolean;
    readonly projectId: string | null;
  } | null;
  /**
   * The configured model the request names, by its server-side key; null: the
   * default LLM. Not the agent's own model, which is unknown when a turn is
   * routed: the family rule reads what today's rollout reads.
   */
  readonly modelKey: string | null;
  /** The request names a conversation: an existing one's record, not the request, decides its executor. Absent: a new one. */
  readonly existing?: boolean;
  /** The body's `harness` field: the default chat's Agent mode asks for `durable` (ASSIST1), the chat switch for `legacy` (CTL-7). */
  readonly requested?: 'durable' | 'legacy' | null;
  /** Admission honoured the person's `legacy` choice: the agent runs on today's loop (CTL-7). */
  readonly classicChosen?: true;
}

/**
 * The facts of a turn its host states none of: it was routed by another
 * decider (or is drained from a queue it was routed into), and its pin
 * records what policy alone says of it.
 */
export const NO_FACTS: RoutingFacts = {
  infrastructureReady: true,
  workerLive: true,
  scheduled: false,
  placement: null,
  modelKey: null,
};

/** The person's classic choice may be honoured: the conversation is new, or its record shows no durable turn (CTL-7). */
export const classicAllowed = (facts: RoutingFacts): boolean =>
  facts.existing !== true ||
  (facts.placement !== null && !facts.placement.durable);

/**
 * The loop mode the assistant's Agent mode (Universal Agent Mode) runs in:
 * `agent` is `quick`, `agent:<mode>` that mode; null for any other mode. The
 * same reading as agent-harness `assistantAgentLoopMode` (legacy's `parseChatMode`).
 */
export const assistantAgentLoop = (chatMode: string): string | null => {
  const mode = chatMode.trim();
  if (!mode.toLowerCase().includes('agent')) return null;
  return mode.split(':')[1] || 'quick';
};

/**
 * What routing reads of the configured models (never how one is reached):
 * the family behind each model key of any kind, and the default LLM's. A
 * family is the configured provider, lower-case; null when the entry names
 * none.
 */
export interface ConfiguredFamilies {
  readonly byKey: ReadonlyMap<string, string | null>;
  readonly ofDefault: string | null;
}

export const NO_FAMILIES: ConfiguredFamilies = {
  byKey: new Map(),
  ofDefault: null,
};

/** The family of the model a turn names; null when it resolves to no configured model. */
export const familyOf = (
  families: ConfiguredFamilies,
  modelKey: string | null,
): string | null =>
  modelKey !== null && modelKey.length > 0
    ? (families.byKey.get(modelKey) ?? null)
    : families.ofDefault;

/** The turn itself, as its route names it. */
export interface RoutedTurn {
  /** Null: the built-in assistant, which has chat modes of its own. */
  readonly agentKey: string | null;
  readonly chatMode: string;
  /** The conversation record's project, else the one a request names for the conversation it creates. */
  readonly projectId: string | null;
}

const DURABLE: Routing = { path: 'durable', reason: null };

const legacy = (reason: LegacyReason): Routing => ({ path: 'legacy', reason });

/** A list row's members; null when the row is unbounded or no layer set it. */
const listed = (value: unknown): readonly string[] | null =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : null;

/**
 * Durable or today's loop for one turn (CPD-10 §2.1): the first row that
 * applies, in `routing.json`'s order. `config` holds the resolved `rollout.*`
 * rows; a switch no layer set is off, and a mode list no layer set admits
 * nothing, so a config the rollout was never folded into routes nothing
 * durable.
 */
export function routingOf(
  config: EffectiveRunConfig,
  families: ConfiguredFamilies,
  turn: RoutedTurn,
  facts: RoutingFacts,
): Routing {
  const row = (name: string): unknown =>
    config.dimensions[`rollout.${name}`]?.value;
  if (!facts.infrastructureReady) return legacy('infrastructure_unavailable');
  if (row('AGENT_HARNESS_KILL_SWITCH') === true) return legacy('kill_switch');
  if (row('ENABLE_AGENT_HARNESS') !== true) return legacy('org_disabled');
  const allowed = listed(row('durable.modelFamilies'));
  if (allowed !== null && allowed.length === 0) {
    return legacy('model_family_not_allowed');
  }
  if (!durableMode(row, turn, facts)) return legacy('chat_mode_not_supported');
  if (allowed !== null) {
    const family = familyOf(families, facts.modelKey);
    if (family === null) return legacy('model_family_unknown');
    if (!allowed.includes(family)) return legacy('model_family_not_allowed');
  }
  if (
    turn.projectId !== null ||
    (facts.placement?.projectId ?? null) !== null
  ) {
    return legacy('project_scope');
  }
  if (
    !facts.workerLive &&
    facts.placement?.durable !== true &&
    !facts.scheduled
  ) {
    return legacy('no_live_worker');
  }
  if (facts.classicChosen === true) return legacy('user_chose_classic');
  return DURABLE;
}

/**
 * The turn's chat mode is one the durable loop runs. The assistant's Agent
 * mode comes with the harness (CTL-5) and runs durably only where the
 * conversation asks for it: a new one by its first turn's `harness: durable`,
 * an existing one by its record (ASSIST1), in a loop mode the agents' list holds.
 */
function durableMode(
  row: (name: string) => unknown,
  turn: RoutedTurn,
  facts: RoutingFacts,
): boolean {
  const agentModes = listed(row('durable.modes')) ?? [];
  if (turn.agentKey !== null) {
    return agentModes.includes(turn.chatMode.trim().toLowerCase());
  }
  const loop = assistantAgentLoop(turn.chatMode);
  if (loop === null) {
    const modes = listed(row('durable.assistantModes')) ?? [];
    return modes.includes(turn.chatMode.trim().toLowerCase());
  }
  const asked =
    facts.existing === true
      ? facts.placement?.durable === true
      : facts.requested === 'durable';
  return asked && agentModes.includes(loop.trim().toLowerCase());
}
