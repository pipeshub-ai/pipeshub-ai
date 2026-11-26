import { ControlPlaneError } from './errors';

/** `CP_FORBIDDEN`: the actor is not one of the request's approvers, or may not decide this way. */
export class ApprovalForbiddenError extends ControlPlaneError {
  constructor(
    message: string,
    readonly reason: string,
  ) {
    super('CP_FORBIDDEN', message);
    this.name = 'ApprovalForbiddenError';
  }
}

/** `CP_APPROVAL_SUPERSEDED`: the target changed since the request (or since the approver looked). */
export class ApprovalSupersededError extends ControlPlaneError {
  constructor(message: string) {
    super('CP_APPROVAL_SUPERSEDED', message);
    this.name = 'ApprovalSupersededError';
  }
}

/** `CP_INVALID_CONFIG`: a change no handler can apply, or a subset outside the change. */
export class ApprovalInvalidError extends ControlPlaneError {
  constructor(message: string) {
    super('CP_INVALID_CONFIG', message);
    this.name = 'ApprovalInvalidError';
  }
}

/**
 * Thrown by a target handler whose target (or the system that holds it)
 * says it moved since the request: the decision cannot apply what the
 * approver saw, so the request is superseded.
 */
export class ApprovalTargetMoved extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ApprovalTargetMoved';
  }
}
