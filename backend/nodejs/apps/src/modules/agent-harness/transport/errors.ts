import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  ForbiddenError,
  HttpError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import {
  ActivityPanelDisabledError,
  ConversationDeletedError,
  HarnessApprovalsRefusedError,
  HarnessConflictError,
  HarnessDecisionRefusedError,
  HarnessEngineProfileRefusedError,
  HarnessForbiddenError,
  HarnessMemoryRefusedError,
  HarnessModelAccessRefusedError,
  HarnessModelAccountRefusedError,
  HarnessNotFoundError,
  HarnessQuotaExceededError,
  HarnessRunConfigUnavailableError,
  HarnessSessionRoleRefusedError,
  HarnessGroupAccessRefusedError,
  HarnessOperatorControlRefusedError,
  HarnessUnavailableError,
  SessionErasedError,
  harnessWireCode,
} from '../domain/errors';
import { ERROR_CODES } from '../contracts/generated/ext/pipeshub/errors';
import {
  STORE_UNAVAILABLE_MESSAGE,
  storeTransient,
} from '../../../libs/repositories/agent-harness/journal-startup/mongo';
import { dependencyHealth } from '../ops/dependency-health';
import { ControlPlaneError, controlPlaneHttpError } from '../../control-plane';

/** A harness refusal as the HTTP error the public routes answer with. */
export function transportError(error: unknown): unknown {
  if (error instanceof ConversationDeletedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof HarnessNotFoundError) {
    return new NotFoundError('Agent run not found');
  }
  if (error instanceof ActivityPanelDisabledError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof HarnessConflictError) {
    return new HttpError(
      `AGENT_HARNESS_${error.code}`,
      error.message,
      ERROR_CODES[error.code].status,
      error.reasonCode !== undefined
        ? { reasonCode: error.reasonCode }
        : undefined,
    );
  }
  if (error instanceof HarnessRunConfigUnavailableError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof HarnessUnavailableError) {
    return new ServiceUnavailableError(error.message);
  }
  if (error instanceof ControlPlaneError) {
    return controlPlaneHttpError(error);
  }
  if (error instanceof HarnessOperatorControlRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
      error.details,
    );
  }
  if (error instanceof HarnessGroupAccessRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof HarnessSessionRoleRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof HarnessEngineProfileRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
      {
        ...(error.dimension !== undefined
          ? { dimension: error.dimension }
          : {}),
        ...(error.layer !== undefined ? { layer: error.layer } : {}),
      },
    );
  }
  if (error instanceof HarnessForbiddenError) {
    return new ForbiddenError(error.message);
  }
  if (error instanceof HarnessModelAccountRefusedError) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
      error.field !== undefined ? { field: error.field } : undefined,
    );
  }
  if (error instanceof HarnessModelAccessRefusedError) {
    const code = error.code.toUpperCase() as Uppercase<typeof error.code>;
    const metadata = {
      ...(error.rule !== null ? { rule: error.rule } : {}),
      ...(error.field !== undefined ? { field: error.field } : {}),
    };
    return new HttpError(
      harnessWireCode(code),
      error.message,
      ERROR_CODES[code].status,
      Object.keys(metadata).length > 0 ? metadata : undefined,
    );
  }
  if (
    error instanceof HarnessMemoryRefusedError ||
    error instanceof HarnessDecisionRefusedError ||
    error instanceof HarnessApprovalsRefusedError
  ) {
    return new HttpError(
      harnessWireCode(error.code),
      error.message,
      ERROR_CODES[error.code].status,
    );
  }
  if (error instanceof SessionErasedError) {
    return new HttpError(
      `AGENT_HARNESS_${error.code}`,
      error.message,
      HTTP_STATUS.GONE,
    );
  }
  if (error instanceof HarnessQuotaExceededError) {
    return new HttpError(
      harnessWireCode('QUOTA_EXCEEDED'),
      error.message,
      ERROR_CODES.QUOTA_EXCEEDED.status,
      // Which quota, so a client says "one answers already" for the active-run cap, not "you sent too many" (QA-AUTO).
      {
        retryAfter: String(error.retryAfterS),
        reasonCode: `${error.quota}_${error.scope}`,
      },
    );
  }
  if (storeTransient(error)) {
    dependencyHealth.report('mongo');
    return new ServiceUnavailableError(STORE_UNAVAILABLE_MESSAGE);
  }
  return error;
}
