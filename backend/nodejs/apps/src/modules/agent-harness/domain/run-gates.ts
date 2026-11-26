import { z } from 'zod/v3';
import { runSpecSchema } from '../contracts/generated/core/run-spec';
import { verificationRecordedEventSchema } from '../contracts/generated/core/events/verification-recorded';

/**
 * Run gates (G-03 dry run, G-04 plan approval, G-05 outcome verification), pinned as `RunSpec.gates`. The worker
 * enforces each where the thing it governs happens; Node only keeps what was asked on the run, pins it, and shows
 * what the run journaled (`tool.outcome.shadow`, `verification.recorded`).
 */
export const RUN_GATES_FEATURE = 'run_gates' as const;

/** The body field a turn names its gates in. */
export const GATES_FIELD = 'gates';

export const runGatesSchema = runSpecSchema.shape.gates.unwrap();
export type RunGates = z.infer<typeof runGatesSchema>;

/**
 * The gates an agent holds every run of it to (WP-31 D), as its authority reports them: plan approval and checks.
 * A dry run is a per-turn try-out, never an agent's.
 */
export const agentGatesSchema = runGatesSchema.omit({ dryRun: true }).strict();
export type AgentGates = z.infer<typeof agentGatesSchema>;

const MAX_CHECKS = 16;

/**
 * What a run is held to: the turn's own gates and its agent's. A gate only narrows, so a turn can add one but never
 * drop the agent's: plan approval if either asks for it, and the agent's checks with the turn's beside them (each
 * once, at most 16), under the agent's own rule for a failed check.
 */
export function admittedGates(
  agent: AgentGates | null | undefined,
  turn: RunGates | null | undefined,
): RunGates | undefined {
  const planApproval =
    agent?.planApproval === 'required' || turn?.planApproval === 'required';
  const verify = mergedVerify(agent?.verify, turn?.verify);
  const gates: RunGates = {
    ...(turn?.dryRun === true ? { dryRun: true as const } : {}),
    ...(planApproval ? { planApproval: 'required' as const } : {}),
    ...(verify !== undefined ? { verify } : {}),
  };
  return Object.keys(gates).length > 0 ? gates : undefined;
}

/**
 * What the turn answering a run's question is held to (WP-64): every gate of the run it answers, and the turn's own.
 * Without it a dry run that asked acted for real once answered. The answer may add a gate, never drop one; its checks
 * join the asking run's, under the asking run's rule for a failed check.
 */
export function answerGates(
  asked: RunGates | null | undefined,
  own: RunGates | null | undefined,
): RunGates | undefined {
  const dryRun = asked?.dryRun === true || own?.dryRun === true;
  const planApproval =
    asked?.planApproval === 'required' || own?.planApproval === 'required';
  const verify = mergedVerify(asked?.verify, own?.verify);
  const gates: RunGates = {
    ...(dryRun ? { dryRun: true as const } : {}),
    ...(planApproval ? { planApproval: 'required' as const } : {}),
    ...(verify !== undefined ? { verify } : {}),
  };
  return Object.keys(gates).length > 0 ? gates : undefined;
}

type VerifyGate = NonNullable<RunGates['verify']>;

function mergedVerify(
  agent: VerifyGate | undefined,
  turn: VerifyGate | undefined,
): VerifyGate | undefined {
  if (agent === undefined || turn === undefined) return agent ?? turn;
  const seen = new Set<string>();
  const both: unknown[] = agent.checks;
  const checks = both.concat(turn.checks as unknown[]).filter((check) => {
    const key = JSON.stringify(check);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return { ...agent, checks: checks.slice(0, MAX_CHECKS) };
}

/** The gates a request body names: absent, a valid set, or a 400 with the first problem. */
export type RequestedGates =
  | { kind: 'none' }
  | { kind: 'gates'; gates: RunGates }
  | { kind: 'invalid'; message: string };

export function requestedGates(body: unknown): RequestedGates {
  const raw =
    body !== null && typeof body === 'object' && !Array.isArray(body)
      ? (body as Record<string, unknown>)[GATES_FIELD]
      : undefined;
  if (raw === undefined || raw === null) return { kind: 'none' };
  const parsed = runGatesSchema.safeParse(raw);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return {
      kind: 'invalid',
      message: `gates${issue?.path.length ? `.${issue.path.join('.')}` : ''}: ${issue?.message ?? 'invalid'}`,
    };
  }
  return Object.keys(parsed.data).length === 0
    ? { kind: 'none' }
    : { kind: 'gates', gates: parsed.data };
}

/**
 * What a child run is held to: every gate its parent holds, and its own. A gate only ever narrows what a run may do,
 * so a child can never drop one (a dry run's helper must be a dry run too); a verify gate is the parent's.
 */
export function inheritedGates(
  parent: RunGates | null | undefined,
  own: RunGates | null | undefined,
): RunGates | undefined {
  const dryRun = parent?.dryRun === true || own?.dryRun === true;
  const planApproval =
    parent?.planApproval === 'required' || own?.planApproval === 'required';
  const verify = own?.verify ?? undefined;
  const gates: RunGates = {
    ...(dryRun ? { dryRun: true as const } : {}),
    ...(planApproval ? { planApproval: 'required' as const } : {}),
    ...(verify !== undefined ? { verify } : {}),
  };
  return Object.keys(gates).length > 0 ? gates : undefined;
}

/** A run's verification as the conversation shows it: the latest `verification.recorded` the run journaled. */
export interface RunVerification {
  status: 'verified' | 'unverified';
  round: number;
  checks: Array<{
    kind: string;
    passed: boolean;
    target?: string;
    reason?: string;
  }>;
}

const verifiedDataSchema = verificationRecordedEventSchema.shape.data;

export function verificationOf(
  events: ReadonlyArray<{ type: string; data?: unknown }>,
): RunVerification | null {
  let latest: RunVerification | null = null;
  for (const event of events) {
    if (event.type !== 'verification.recorded') continue;
    const data = verifiedDataSchema.safeParse(event.data);
    if (!data.success) continue;
    latest = {
      status: data.data.status,
      round: data.data.round,
      checks: data.data.checks.map((check) => ({ ...check })),
    };
  }
  return latest;
}
