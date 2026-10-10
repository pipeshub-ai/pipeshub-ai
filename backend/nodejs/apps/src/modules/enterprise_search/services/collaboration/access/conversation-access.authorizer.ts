import { DomainHttpError } from '../../../../../libs/errors/domain-http.error';
import {
  DecisionLogEntry,
  IDecisionLog,
  NOOP_DECISION_LOG,
} from '../../../../authz/decision-log';
import { AccessPath, ProjectFacts } from '../../../../authz/domain/types';
import { COLLAB_ERROR_CODES } from '../domain/errors';
import {
  ConversationNotFoundError,
  ConversationOwnerOnlyError,
  ConversationReadOnlyError,
  OwnerInactiveError,
  OwnerStatusUnavailableError,
  RegenerateNotAllowedError,
  ResumeNotAllowedError,
  TeamResolutionUnavailableError,
} from '../domain/errors';
import {
  Caller,
  ConversationAccessFields,
  ConversationOperation,
  GrantedRole,
} from '../domain/types';
import {
  AccessDecision,
  AccessPolicyOptions,
  OperationContext,
  decide,
  resolveRole,
} from './conversation-access.policy';

export function errorForDecision(
  decision: Extract<AccessDecision, { allowed: false }>,
): DomainHttpError {
  return errorForCode(decision.code);
}

/** Unknown codes read as not-found, so a stray code can never widen what a client learns. */
export function errorForCode(code: string): DomainHttpError {
  switch (code) {
    case COLLAB_ERROR_CODES.READ_ONLY:
      return new ConversationReadOnlyError();
    case COLLAB_ERROR_CODES.OWNER_ONLY:
      return new ConversationOwnerOnlyError();
    case COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED:
      return new RegenerateNotAllowedError();
    case COLLAB_ERROR_CODES.RESUME_NOT_ALLOWED:
      return new ResumeNotAllowedError();
    case COLLAB_ERROR_CODES.OWNER_INACTIVE:
      return new OwnerInactiveError();
    case COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE:
      return new TeamResolutionUnavailableError();
    case COLLAB_ERROR_CODES.OWNER_STATUS_UNAVAILABLE:
      return new OwnerStatusUnavailableError();
    default:
      return new ConversationNotFoundError();
  }
}

export interface AuthorizeInput {
  op: ConversationOperation;
  conversationId: string;
  session: ConversationAccessFields;
  caller: Caller;
  flags: AccessPolicyOptions;
  requestId: string;
  project?: ProjectFacts | null;
  ctx?: OperationContext;
}

export interface Authorization {
  role: GrantedRole;
  via: readonly AccessPath[];
}

/** Resolve, decide and log in one place so no caller can decide without leaving a trace. */
export class ConversationAccessAuthorizer {
  constructor(private readonly log: IDecisionLog = NOOP_DECISION_LOG) {}

  evaluate(input: AuthorizeInput): {
    decision: AccessDecision;
    via: readonly AccessPath[];
  } {
    const resolved = resolveRole(input.session, input.caller, {
      collab: input.flags.collab,
      op: input.op,
      project: input.project,
    });
    const unresolved = 'unresolved' in resolved;
    const via = unresolved ? [] : resolved.via;
    const decision = decide(
      input.op,
      unresolved ? 'unresolved' : resolved.role,
      {
        editorsCanInvite: input.session.settings?.editorsCanInvite === true,
        ...input.ctx,
      },
    );
    this.log.record(this.entry(input, decision, via));
    return { decision, via };
  }

  assertAllowed(input: AuthorizeInput): Authorization {
    const { decision, via } = this.evaluate(input);
    if (!decision.allowed) {
      throw errorForDecision(decision);
    }
    return { role: decision.role, via };
  }

  private entry(
    input: AuthorizeInput,
    decision: AccessDecision,
    via: readonly AccessPath[],
  ): DecisionLogEntry {
    return {
      subject: input.caller.userId,
      action: input.op,
      resource: `chat:${input.conversationId}`,
      decision: decision.allowed ? 'allow' : 'deny',
      role: decision.allowed ? decision.role : 'none',
      via: via.map((p) => `${p.type}:${p.ref}`),
      aclVersion: input.session.aclVersion,
      requestId: input.requestId,
      ...(!decision.allowed && { code: decision.code }),
    };
  }
}
