import {
  ERROR_CODES,
  problemSchema,
  type ErrorCode,
  type Problem,
} from './generated';

/** The first rule a body breaks (FR-38); the same names in Python and the frontend. */
export type ProblemRefusal =
  | 'schema'
  | 'unknown_code'
  | 'status'
  | 'retryable'
  | 'remediation';

export type ProblemReading =
  | { problem: Problem; refusal: null }
  | { problem: null; refusal: ProblemRefusal };

const refused = (refusal: ProblemRefusal): ProblemReading => ({
  problem: null,
  refusal,
});

const isCatalogued = (code: string): code is ErrorCode =>
  Object.prototype.hasOwnProperty.call(ERROR_CODES, code);

/**
 * A `Problem` held to the one catalogue: the schema accepts any `CP_*` code at
 * any status, so the reader also requires the catalogue's status,
 * retryability and remediation actor for the code. Python
 * (`app.control_plane.problems`) and Node (`control-plane/domain/problem.ts`) read by the same rules,
 * held by `contracts/control-plane/vectors/problems.json`.
 */
export function readProblem(body: unknown): ProblemReading {
  const parsed = problemSchema.safeParse(body);
  if (!parsed.success) return refused('schema');
  // Without strictNullChecks z.infer is all-optional; the written type is held equal to it under strict (CPD0-17).
  const problem = parsed.data as Problem;
  if (!isCatalogued(problem.code)) return refused('unknown_code');
  const entry: { status: number | null; retryable: boolean; remediation?: string } =
    ERROR_CODES[problem.code];
  if (problem.status !== entry.status) return refused('status');
  if (problem.retryable !== entry.retryable) return refused('retryable');
  if (
    problem.remediation != null &&
    problem.remediation.actor !== entry.remediation
  ) {
    return refused('remediation');
  }
  return { problem, refusal: null };
}
