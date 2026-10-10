import { ClientSession } from 'mongoose';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { EmailIntent } from '../../../../notification/service/notification-email.dispatcher';
import {
  CollaboratorLimitError,
  ConversationNotFoundError,
} from '../domain/errors';
import { principalKey, toCollaborator } from '../domain/collaborator.mapper';
import { AccessLevel, Principal, PrincipalKey } from '../domain/types';
import { ConversationAccessGrant } from '../http/conversation-context';
import { CollaborationEvent } from '../notify/events';
import { ICollaboratorRepository } from '../persistence/collaborator.repository';
import { isOrgWide, PlannedOp } from '../rules/upsert-plan';
import { chatAudit } from './audit-events';
import { MutationEffects } from './mutation-effects';
import { auditContextOf, eventBaseOf, scopeOf } from './mutation-context';

const levelsByPrincipal = (
  grant: ConversationAccessGrant,
): Map<PrincipalKey, AccessLevel> =>
  new Map(
    (grant.session.sharedWith ?? []).flatMap((row) => {
      const c = toCollaborator(row);
      return c ? [[principalKey(c.principal), c.accessLevel] as const] : [];
    }),
  );

export interface WriteRequest {
  grant: ConversationAccessGrant;
  identity: CallerIdentity;
  /** Undefined on a standalone server. */
  dbSession: ClientSession | undefined;
}

/** Applies planned collaborator changes one by one, each followed by its audit row; the events go out together at the end. */
export class CollaboratorWriter {
  constructor(
    private readonly repo: ICollaboratorRepository,
    private readonly effects: MutationEffects,
  ) {}

  async apply(
    req: WriteRequest,
    ops: readonly PlannedOp[],
    extras: {
      note?: string;
      emailIntents: ReadonlyMap<string, EmailIntent>;
    },
  ): Promise<void> {
    const { grant, dbSession } = req;
    const base = eventBaseOf(grant);
    const audit = auditContextOf(grant, req.identity);
    const scope = scopeOf(grant, grant.role === 'owner');
    const events: CollaborationEvent[] = [];
    for (const op of ops) {
      if (op.kind === 'add') {
        const out = await this.repo.add(
          scope,
          {
            principal: op.principal,
            accessLevel: op.accessLevel,
            addedBy: grant.caller.userId,
          },
          { session: dbSession },
        );
        if (out.status === 'not_found') {
          throw new ConversationNotFoundError();
        }
        if (out.status === 'limit') {
          throw new CollaboratorLimitError(out.max);
        }
        if (out.status !== 'added') {
          continue;
        }
        await this.effects.recordAudit(
          chatAudit(audit, 'chat.share', {
            principal: op.principal,
            after: { accessLevel: op.accessLevel },
            aclVersion: out.aclVersion,
          }),
          dbSession,
        );
        if (!isOrgWide(op.principal, grant.caller.orgId)) {
          const intent =
            op.principal.type === 'user'
              ? extras.emailIntents.get(op.principal.userId)
              : undefined;
          events.push({
            type: 'chat.shared',
            ...base,
            principal: op.principal,
            accessLevel: op.accessLevel,
            aclVersion: out.aclVersion,
            ...(extras.note !== undefined && { note: extras.note }),
            ...(intent && { emailIntent: intent }),
          });
        }
        continue;
      }
      const out = await this.repo.changeLevel(scope, op.principal, op.to, {
        session: dbSession,
      });
      if (out.status === 'not_found') {
        throw new ConversationNotFoundError();
      }
      if (out.status !== 'applied') {
        continue;
      }
      await this.effects.recordAudit(
        chatAudit(audit, 'chat.accessChange', {
          principal: op.principal,
          before: { accessLevel: op.from },
          after: { accessLevel: op.to },
          aclVersion: out.aclVersion,
        }),
        dbSession,
      );
      if (op.to === 'write' && !isOrgWide(op.principal, grant.caller.orgId)) {
        events.push({
          type: 'chat.accessChanged',
          ...base,
          principal: op.principal,
          accessLevel: 'write',
          aclVersion: out.aclVersion,
        });
      }
    }
    await this.effects.publish(events, dbSession, req.identity);
  }

  /** Removing a principal that is not there is a no-op; the principal is never looked up, so a deleted team can be removed. */
  async remove(
    req: WriteRequest,
    principals: readonly Principal[],
  ): Promise<void> {
    const { grant, dbSession } = req;
    const base = eventBaseOf(grant);
    const audit = auditContextOf(grant, req.identity);
    const scope = scopeOf(grant, true);
    const levels = levelsByPrincipal(grant);
    const events: CollaborationEvent[] = [];
    for (const principal of principals) {
      const out = await this.repo.remove(scope, principal, {
        session: dbSession,
      });
      if (out.status === 'not_found') {
        throw new ConversationNotFoundError();
      }
      if (out.status !== 'applied') {
        continue;
      }
      await this.effects.recordAudit(
        chatAudit(audit, 'chat.unshare', {
          principal,
          before: { accessLevel: levels.get(principalKey(principal)) },
          aclVersion: out.aclVersion,
        }),
        dbSession,
      );
      events.push({ type: 'chat.unshared', ...base, principal });
    }
    await this.effects.publish(events, dbSession, req.identity);
  }
}
