import { DomainHttpError } from '../../../libs/errors/domain-http.error';

export const AGENT_HANDLE_ERROR_CODES = [
  'HANDLE_TAKEN',
  'HANDLE_RESERVED',
  'HANDLE_INVALID',
] as const;

/** Create-time access refusals: `ids` names the knowledge sources or tools to untick. */
export const AGENT_ACCESS_ERROR_CODES = [
  'INVALID_KNOWLEDGE',
  'INVALID_TOOLSET',
  'SERVICE_ACCOUNT_NOT_ALLOWED',
] as const;

const RELAYED_CODES: readonly string[] = [
  ...AGENT_HANDLE_ERROR_CODES,
  ...AGENT_ACCESS_ERROR_CODES,
];

type AgentHandleErrorCode =
  | (typeof AGENT_HANDLE_ERROR_CODES)[number]
  | (typeof AGENT_ACCESS_ERROR_CODES)[number];

/** A problem with the request's handle or attachments that the client can fix. `suggestion` is the first free alternative for HANDLE_TAKEN. */
export class AgentHandleError extends DomainHttpError {
  constructor(
    code: AgentHandleErrorCode,
    message: string,
    statusCode: number,
    suggestion?: string,
    ids?: readonly string[],
  ) {
    const details = {
      ...(suggestion ? { suggestion } : {}),
      ...(ids && ids.length > 0 ? { ids } : {}),
    };
    super(
      code,
      message,
      statusCode,
      Object.keys(details).length > 0 ? details : undefined,
    );
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

/**
 * Python answers handle and access problems with `detail: {code, message, suggestion?, ids?}`.
 * The generic backend-error mapper would flatten that into a JSON string, so
 * the agent create/update controllers try this first.
 */
export const mapAgentHandleError = (
  aiResponse: unknown,
): AgentHandleError | undefined => {
  if (!isRecord(aiResponse) || !isRecord(aiResponse.data)) return undefined;
  const detail = aiResponse.data.detail;
  if (!isRecord(detail)) return undefined;
  const code = detail.code;
  const status = aiResponse.statusCode;
  if (
    typeof code !== 'string' ||
    !RELAYED_CODES.includes(code) ||
    (status !== 400 && status !== 409)
  ) {
    return undefined;
  }
  const message =
    typeof detail.message === 'string' && detail.message
      ? detail.message
      : 'Invalid agent handle';
  const suggestion =
    typeof detail.suggestion === 'string' ? detail.suggestion : undefined;
  const ids = Array.isArray(detail.ids)
    ? detail.ids.filter((id): id is string => typeof id === 'string')
    : undefined;
  return new AgentHandleError(
    code as AgentHandleErrorCode,
    message,
    status,
    suggestion,
    ids,
  );
};
