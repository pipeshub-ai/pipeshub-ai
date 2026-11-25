/**
 * Run gates (G-03 dry run, G-04 plan approval, G-05 verification) as the chat asks for them. A turn turns them on in
 * the composer's "+" menu (shown as chips once on, and cleared once the message is sent); an agent's own plan
 * approval and checks come from its builder (`gates` on the agent). Admission merges the two and a gate only narrows:
 * a turn can add one, never drop the agent's. A dry run is a per-turn choice only.
 */
import { create } from 'zustand';
import type { AgentGates, VerifyCheck, VerifyGate } from '@/app/(main)/agents/agent-gates';

export type { AgentGates, VerifyCheck, VerifyGate };

/** What the stream body's `gates` carries (Node's `turnGatesSchema`). */
export interface TurnGatesRequest {
  dryRun?: true;
  planApproval?: 'required';
  verify?: VerifyGate;
}

export type TurnGateKey = 'dryRun' | 'planApproval' | 'verify';

export interface TurnGatesChoice {
  dryRun: boolean;
  planApproval: boolean;
  verify: boolean;
}

export const NO_TURN_GATES: TurnGatesChoice = Object.freeze({ dryRun: false, planApproval: false, verify: false });

/** "Check the result" for a chat whose agent sets no checks: what the answer quotes must be in its sources. */
export const CITATION_CHECKS: VerifyGate = Object.freeze({
  checks: [{ kind: 'citations' as const }],
  onFail: 'continue' as const,
  maxRounds: 2,
}) as VerifyGate;

interface RunGatesState {
  /** The next message's own gates; cleared once it is sent. */
  turn: TurnGatesChoice;
  set: (key: TurnGateKey, on: boolean) => void;
  clear: () => void;
}

export const useRunGatesStore = create<RunGatesState>((set) => ({
  turn: { ...NO_TURN_GATES },
  set: (key, on) => set((s) => ({ turn: { ...s.turn, [key]: on } })),
  clear: () => set({ turn: { ...NO_TURN_GATES } }),
}));

/** What the agent itself always holds runs to; its chips show on and cannot be turned off. */
export function agentGateLocks(agent: AgentGates | null | undefined): Pick<TurnGatesChoice, 'planApproval' | 'verify'> {
  return { planApproval: agent?.planApproval === 'required', verify: (agent?.verify?.checks.length ?? 0) > 0 };
}

/**
 * The `gates` field of the next message: the turn's own, without what the agent already holds every run to (admission
 * adds the agent's). Empty: no field, so a chat with no gate stays on whatever path served it.
 */
export function turnGatesField(
  turn: TurnGatesChoice,
  agent: AgentGates | null | undefined,
): { gates?: TurnGatesRequest } {
  const locks = agentGateLocks(agent);
  const gates: TurnGatesRequest = {
    ...(turn.dryRun ? { dryRun: true as const } : {}),
    ...(turn.planApproval && !locks.planApproval ? { planApproval: 'required' as const } : {}),
    ...(turn.verify && !locks.verify ? { verify: CITATION_CHECKS } : {}),
  };
  return Object.keys(gates).length > 0 ? { gates } : {};
}
