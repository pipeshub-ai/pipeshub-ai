/**
 * An agent's own run gates (G-04 plan approval, G-05 verification), set in its builder's "Safety and checks" section
 * and versioned with it (OWD-14). Every run of the agent is held to them; a dry run is never an agent default.
 * The shape is the RunSpec's `gates` without `dryRun`.
 */

export const FILE_FORMATS = ['pdf', 'docx', 'xlsx', 'pptx', 'csv', 'json', 'png', 'jpeg', 'zip', 'text'] as const;
export type FileFormat = (typeof FILE_FORMATS)[number];

export type VerifyCheck =
  | { kind: 'file'; path: string; format?: FileFormat }
  | { kind: 'json_schema'; path: string; schema: Record<string, unknown> }
  | { kind: 'citations' }
  | { kind: 'command'; argv: string[]; timeoutS?: number };

export interface VerifyGate {
  checks: VerifyCheck[];
  onFail: 'flag' | 'continue';
  maxRounds?: number;
}

export interface AgentGates {
  planApproval?: 'required';
  verify?: VerifyGate;
}

export const MAX_CHECKS = 16;
export const DEFAULT_COMMAND_TIMEOUT_S = 120;
/** "Tell the agent and let it fix it (up to 2 times)". */
export const FIX_ROUNDS = 2;

/** A workspace path as the gate takes it: relative, no '.' or '..' segment (the RunSpec's path rule). */
const PATH = /^(?:\.?[A-Za-z0-9_-][A-Za-z0-9_. -]{0,127}\/){0,15}\.?[A-Za-z0-9_-][A-Za-z0-9_. -]{0,127}$/;

export const isWorkspacePath = (path: string): boolean => PATH.test(path) && path.length <= 512;

/** A command line split the way a person reads it: on spaces, keeping "quoted words" together. */
export function commandArgv(line: string): string[] {
  const out: string[] = [];
  const pattern = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line)) !== null) out.push(match[1] ?? match[2] ?? match[3] ?? '');
  return out.filter((word) => word.length > 0).slice(0, 32);
}

export const commandLine = (argv: readonly string[]): string =>
  argv.map((word) => (/\s/.test(word) ? `"${word}"` : word)).join(' ');

/** Why a check cannot be saved, as a locale key, or null. */
export function checkProblem(check: VerifyCheck): string | null {
  switch (check.kind) {
    case 'file':
    case 'json_schema':
      return isWorkspacePath(check.path.trim()) ? null : 'agentBuilder.gates.problem.path';
    case 'command':
      return check.argv.length > 0 ? null : 'agentBuilder.gates.problem.command';
    default:
      return null;
  }
}

/** The agent's stored gates, read defensively (an older agent has none; anything else is dropped). */
export function agentGatesOf(raw: unknown): AgentGates | null {
  if (raw === null || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const gates: AgentGates = {};
  if (value['planApproval'] === 'required') gates.planApproval = 'required';
  const verify = value['verify'] as Record<string, unknown> | undefined;
  if (verify && Array.isArray(verify['checks']) && verify['checks'].length > 0) {
    gates.verify = {
      checks: verify['checks'] as VerifyCheck[],
      onFail: verify['onFail'] === 'flag' ? 'flag' : 'continue',
      ...(typeof verify['maxRounds'] === 'number' ? { maxRounds: verify['maxRounds'] } : {}),
    };
  }
  return Object.keys(gates).length > 0 ? gates : null;
}

/**
 * `gates` as the save sends it: the edited gates; `{}` when the author turned off every gate of an agent that had
 * some (so the server clears them); nothing at all for an agent that never had any, whose payload stays as it was.
 */
export function agentGatesPayload(edited: unknown, stored: unknown): { gates?: AgentGates } {
  const gates = agentGatesOf(edited);
  if (gates) return { gates };
  return agentGatesOf(stored) ? { gates: {} } : {};
}
