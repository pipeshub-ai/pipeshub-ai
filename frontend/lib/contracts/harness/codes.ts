import { ERROR_CODES, WIRE_PREFIX, type ErrorCode, type ErrorCodeOf } from './generated/ext/pipeshub/errors';

/** A harness catalogue code as Node's error middleware answers it (`HTTP_AGENT_HARNESS_<code>`). */
export const harnessHttpCode = (code: ErrorCode): string => `HTTP_${WIRE_PREFIX}${code}`;

/** Every catalogue code of one group, at run time (ERR-UX: a refusal map is checked against its group). */
export const harnessCodesOf = <G extends string>(group: G): ReadonlySet<ErrorCodeOf<G>> =>
  new Set(
    (Object.keys(ERROR_CODES) as ErrorCode[]).filter(
      (code) => (ERROR_CODES[code] as { readonly group?: string }).group === group,
    ) as ErrorCodeOf<G>[],
  );
