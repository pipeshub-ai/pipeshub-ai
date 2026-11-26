import type { ResolvedRunConfig } from '../../control-plane';

/** C4.10: the run-scoped dimensions Node itself applies, as the run's pin resolved them. */
export const PERSON_WAIT_S = 'interactions.waitS';
export const AUTO_RESUME = 'recovery.autoResume';
export const SHOW_REASONING = 'ui.reasoning';
export const SHOW_TERMINAL = 'ui.terminal';
export const CONVERSATION_GRANTS = 'interactions.conversationGrants';

/** What a person is told when their org's runs never hold an answer for the whole conversation. */
export const CONVERSATION_GRANTS_OFF_MESSAGE =
  'Your organization asks before each of these actions, so this one can only be allowed once';

/**
 * What a run document keeps of its pin for the store's own pipelines, which
 * cannot read the pin's canonical JSON: absent fields are the release's (the
 * deployment's wait, resume on).
 */
export interface RunPolicyFields {
  /** `interactions.waitS`: how long a person's answer is awaited; absent: the deployment's. */
  personWaitS?: number;
  /** `recovery.autoResume` off: a run whose worker was lost ends instead of resuming. */
  autoResume?: false;
  /**
   * `interactions.conversationGrants` off: no answer is held for the conversation. The worker never offers one;
   * Node refuses one all the same, so a worker that offers it by mistake cannot widen the org's policy.
   */
  conversationGrants?: false;
}

type Dimensions = Readonly<Record<string, { value?: unknown } | undefined>>;

const positiveInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0;

const dimensionsOf = (resolved: unknown): Dimensions => {
  const config = (
    resolved as
      { effectiveConfig?: { dimensions?: unknown } } | null | undefined
  )?.effectiveConfig;
  const dimensions = config?.dimensions;
  return typeof dimensions === 'object' && dimensions !== null
    ? (dimensions as Dimensions)
    : {};
};

/** The fields a run document keeps of `resolved`; none for a run without a pin. */
export function runPolicyFields(
  resolved: ResolvedRunConfig | null | undefined,
): RunPolicyFields {
  if (resolved === null || resolved === undefined) return {};
  const dimensions = dimensionsOf(resolved);
  const wait = dimensions[PERSON_WAIT_S]?.value;
  return {
    ...(positiveInteger(wait) ? { personWaitS: wait } : {}),
    ...(dimensions[AUTO_RESUME]?.value === false
      ? { autoResume: false as const }
      : {}),
    ...(dimensions[CONVERSATION_GRANTS]?.value === false
      ? { conversationGrants: false as const }
      : {}),
  };
}

/** The window a run waiting for a person stays answerable: the deployment's, or less where its pin says so. */
export const personWaitWindowMs = (
  run: RunPolicyFields,
  deploymentMs: number,
): number =>
  positiveInteger(run.personWaitS)
    ? Math.min(deploymentMs, run.personWaitS * 1000)
    : deploymentMs;

/** What a conversation shows of a run (`ui.*`); a run without a pin shows both. */
export interface RunDisplay {
  reasoning: boolean;
  terminal: boolean;
}

export const SHOW_ALL: RunDisplay = Object.freeze({
  reasoning: true,
  terminal: true,
});

/** `ui.reasoning` and `ui.terminal` of a pin as a run's spec carries it (`x.pipeshub.resolved`). */
export function runDisplay(resolved: unknown): RunDisplay {
  const dimensions = dimensionsOf(resolved);
  return {
    reasoning: dimensions[SHOW_REASONING]?.value !== false,
    terminal: dimensions[SHOW_TERMINAL]?.value !== false,
  };
}
