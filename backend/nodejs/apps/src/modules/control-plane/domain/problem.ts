import {
  ERROR_CODES,
  problemSchema,
  type ErrorCode,
  type LayerName,
  type Problem,
  type RemediationActor,
} from '../contracts/generated';

/** The first rule a body breaks (FR-38); the same names in Python and the frontend. */
export type ProblemRefusal =
  'schema' | 'unknown_code' | 'status' | 'retryable' | 'remediation';

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
 * (`app.control_plane.problems`) and the frontend read by the same rules,
 * held by `contracts/control-plane/vectors/problems.json`.
 */
export function readProblem(body: unknown): ProblemReading {
  const parsed = problemSchema.safeParse(body);
  if (!parsed.success) return refused('schema');
  const problem = parsed.data;
  if (!isCatalogued(problem.code)) return refused('unknown_code');
  const entry: {
    status: number | null;
    retryable: boolean;
    remediation?: string;
  } = ERROR_CODES[problem.code];
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

export type ProblemSubject = NonNullable<Problem['subject']>;

export interface ProblemFields {
  readonly subject?: ProblemSubject;
  readonly dimension?: string | null;
  readonly layer?: LayerName | null;
  readonly action?: string;
}

/** A `Problem` built from the catalogue, so `readProblem` accepts it; a code without a status is never a `Problem`. */
export function problem(
  code: ErrorCode,
  message: string,
  fields: ProblemFields = {},
): Problem {
  const entry: {
    status: number | null;
    retryable: boolean;
    remediation?: RemediationActor;
  } = ERROR_CODES[code];
  if (entry.status === null) {
    throw new Error(
      `${code} has no status in the catalogue, so it is never a Problem`,
    );
  }
  return {
    code,
    status: entry.status,
    message,
    retryable: entry.retryable,
    ...(fields.subject === undefined ? {} : { subject: fields.subject }),
    dimension: fields.dimension ?? null,
    layer: fields.layer ?? null,
    remediation:
      fields.action === undefined || entry.remediation === undefined
        ? null
        : { action: fields.action, actor: entry.remediation },
  };
}

export const isProblem = (value: unknown): value is Problem =>
  typeof value === 'object' &&
  value !== null &&
  typeof (value as { code?: unknown }).code === 'string' &&
  typeof (value as { status?: unknown }).status === 'number';
