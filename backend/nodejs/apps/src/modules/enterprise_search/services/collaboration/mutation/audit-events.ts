import { Types } from 'mongoose';
import { AuditEventInput } from '../../../../../libs/audit/audit.writer';
import { Principal } from '../domain/types';

export type ChatAuditAction =
  | 'chat.share'
  | 'chat.unshare'
  | 'chat.accessChange'
  | 'chat.settingsChange'
  | 'chat.ownershipTransfer'
  | 'chat.leave';

export interface AuditContext {
  orgId: string;
  actorUserId: string;
  sessionId: string;
  requestId?: string;
}

const principalOf = (p: Principal): AuditEventInput['principal'] =>
  p.type === 'user'
    ? { principalType: 'user', principalId: p.userId }
    : { principalType: 'team', principalId: p.teamId };

export function chatAudit(
  ctx: AuditContext,
  action: ChatAuditAction,
  detail: {
    principal?: Principal;
    before?: unknown;
    after?: Record<string, unknown>;
    aclVersion: number;
  },
): AuditEventInput {
  return {
    orgId: new Types.ObjectId(ctx.orgId),
    actorUserId: new Types.ObjectId(ctx.actorUserId),
    action,
    targetType: 'chatSession',
    targetId: ctx.sessionId,
    ...(detail.principal && { principal: principalOf(detail.principal) }),
    ...(detail.before !== undefined && { before: detail.before }),
    ...(detail.after !== undefined && { after: detail.after }),
    aclVersion: detail.aclVersion,
    ...(ctx.requestId !== undefined && { requestId: ctx.requestId }),
  };
}
