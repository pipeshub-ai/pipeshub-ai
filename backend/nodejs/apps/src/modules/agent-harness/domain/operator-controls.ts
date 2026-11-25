/**
 * Operator controls (ADR-OPS-01): one record, a stop on one scope, for every
 * switch an operator turns off. Pure: admission, enforcement and the sweeper
 * decide from these functions, whichever source holds the controls.
 */
import { z } from 'zod/v3';

export const OPERATOR_SCOPE_KINDS = [
  'session',
  'agent',
  'org',
  'engine_version',
  'instance',
  'egress',
] as const;
export type OperatorScopeKind = (typeof OPERATOR_SCOPE_KINDS)[number];

export const OPERATOR_REASONS = [
  'security',
  'cost',
  'incident',
  'maintenance',
  'other',
] as const;
export type OperatorReason = (typeof OPERATOR_REASONS)[number];

export const MAX_OPERATOR_NOTE_CHARS = 500;

/** What a control does to the runs it covers: cancel them (`stop`) or suspend them until allowed (`retire`, D6). */
export const OPERATOR_EFFECTS = ['stop', 'retire'] as const;
export type OperatorEffect = (typeof OPERATOR_EFFECTS)[number];
export const effectOf = (control: OperatorControl): OperatorEffect =>
  control.effect ?? 'stop';
/** The suspend reason a retired run waits on (run.suspended, SUSPEND_REASONS). */
export const RETIRED = 'retired';
/** AD-06 / C11.06: every stop takes effect within this; OPS-02 holds the arithmetic to it. */
export const OPERATOR_STOP_BOUND_MS = 60_000;
/** Who set the deployment's env switch (`AGENT_HARNESS_KILL_SWITCH`), which no admin can clear. */
export const DEPLOYMENT_SETTER = 'deployment';

const id = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/);
const engineName = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9][a-z0-9_.+-]*$/);

export const operatorScopeSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('session'), sessionId: id }).strict(),
  z.object({ kind: z.literal('agent'), agentKey: id }).strict(),
  z.object({ kind: z.literal('org') }).strict(),
  z
    .object({
      kind: z.literal('engine_version'),
      engine: z.object({ kind: engineName, version: engineName }).strict(),
    })
    .strict(),
  z.object({ kind: z.literal('instance') }).strict(),
  z.object({ kind: z.literal('egress') }).strict(),
]);
export type OperatorScope = z.infer<typeof operatorScopeSchema>;

export const operatorControlSchema = z
  .object({
    id: z.string().min(1).max(400),
    scope: operatorScopeSchema,
    orgId: z.string().min(1).max(128).nullable(),
    reason: z.enum(OPERATOR_REASONS),
    note: z.string().max(MAX_OPERATOR_NOTE_CHARS).nullable(),
    setBy: z.string().min(1).max(160),
    setAt: z.number().int().nonnegative(),
    /** D6: `retire` suspends the runs an engine-version control covers instead of cancelling them; absent: `stop`. */
    effect: z.enum(OPERATOR_EFFECTS).optional(),
  })
  .strict();
export type OperatorControl = z.infer<typeof operatorControlSchema>;

/** The run error a stop by an operator control ends with (run-error-codes.json); the web app words it per scope. */
export const OPERATOR_STOPPED = 'operator_stopped';
export const OPERATOR_STOPPED_MESSAGE =
  'Your admin stopped this agent work for now.';

/**
 * A run an operator control stopped, as its ending says it (D5): the one typed value the stream's run error and
 * the saved failure are both built from, so an outcome presenter (ADR-RUN-01 D1) can take it over unchanged.
 */
export interface OperatorStopOutcome {
  code: typeof OPERATOR_STOPPED;
  message: string;
  stop: OperatorStop;
}

export const operatorStopOutcome = (
  stop: OperatorStop,
): OperatorStopOutcome => ({
  code: OPERATOR_STOPPED,
  message: OPERATOR_STOPPED_MESSAGE,
  stop: { scope: stop.scope, reason: stop.reason },
});

/** What `run.cancel_requested` carries of the control that asked (D5). */
export interface OperatorStop {
  scope: OperatorScopeKind;
  reason: OperatorReason;
}

/** The fields of a run a control is matched on; `engine` is its pinned engine, null for a loop run. */
export interface StoppableRun {
  orgId: string;
  sessionId: string;
  agentKey?: string | null;
  engine: { kind: string; version: string } | null;
}

/** Whether a control holds for `orgId`: its own org's, or one with no org (instance-wide). */
export const holdsFor = (control: OperatorControl, orgId: string): boolean =>
  control.orgId === null || control.orgId === orgId;

export function covers(control: OperatorControl, run: StoppableRun): boolean {
  const { scope } = control;
  switch (scope.kind) {
    case 'session':
      return run.orgId === control.orgId && run.sessionId === scope.sessionId;
    case 'agent':
      return run.orgId === control.orgId && run.agentKey === scope.agentKey;
    case 'org':
      return run.orgId === control.orgId;
    case 'engine_version':
      return (
        holdsFor(control, run.orgId) &&
        run.engine !== null &&
        run.engine.kind === scope.engine.kind &&
        run.engine.version === scope.engine.version
      );
    case 'instance':
      return true;
    case 'egress':
      // An engine's sandbox reaches the model gateway only through the egress proxy.
      return holdsFor(control, run.orgId) && run.engine !== null;
  }
}

/** Narrowest first, so a person is told the most specific reason. */
const PRECEDENCE: readonly OperatorScopeKind[] = [
  'session',
  'agent',
  'engine_version',
  'egress',
  'org',
  'instance',
];
const rank = (control: OperatorControl): number =>
  PRECEDENCE.indexOf(control.scope.kind);

export function coveringControls(
  run: StoppableRun,
  controls: readonly OperatorControl[],
): OperatorControl[] {
  return controls
    .filter((control) => covers(control, run))
    .sort((a, b) =>
      rank(a) !== rank(b) ? rank(a) - rank(b) : a.id.localeCompare(b.id),
    );
}

export const stopOf = (control: OperatorControl): OperatorStop => ({
  scope: control.scope.kind,
  reason: control.reason,
});

/** The env switch as the instance control it is: shown, never cleared from the UI. */
export const deploymentInstanceControl = (): OperatorControl => ({
  id: 'instance',
  scope: { kind: 'instance' },
  orgId: null,
  reason: 'incident',
  note: null,
  setBy: DEPLOYMENT_SETTER,
  setAt: 0,
});

/** Whether every harness turn of the org takes today's chat path: an org or instance stop. */
export const orgStopped = (
  controls: readonly OperatorControl[],
  orgId: string,
): boolean =>
  controls.some(
    (control) =>
      control.scope.kind === 'instance' ||
      (control.scope.kind === 'org' && control.orgId === orgId),
  );

/** A session or agent stop admission refuses a turn for (the narrowest), or null. */
export function admissionStop(
  controls: readonly OperatorControl[],
  turn: { orgId: string; sessionId: string; agentKey?: string | null },
): OperatorControl | null {
  return (
    coveringControls({ ...turn, engine: null }, controls).find(
      (control) =>
        control.scope.kind === 'session' || control.scope.kind === 'agent',
    ) ?? null
  );
}

/** `kind@version` of every engine version stopped for `orgId`. */
export const stoppedEngines = (
  controls: readonly OperatorControl[],
  orgId: string,
): ReadonlySet<string> =>
  new Set(
    controls.flatMap((control) =>
      control.scope.kind === 'engine_version' && holdsFor(control, orgId)
        ? [`${control.scope.engine.kind}@${control.scope.engine.version}`]
        : [],
    ),
  );

export const egressStopped = (
  controls: readonly OperatorControl[],
  orgId: string,
): boolean =>
  controls.some(
    (control) => control.scope.kind === 'egress' && holdsFor(control, orgId),
  );
