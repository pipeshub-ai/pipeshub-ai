import { ClientSession } from 'mongoose';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { NotificationBrokerMessage } from '../../../../notification/utils/notification-payload.resolver';
import { Principal } from '../domain/types';
import { CollaborationEvent, CollaborationEventType } from './events';
import { INotificationArchiver } from './notification-archiver';
import { toNotificationMessage } from './notification-messages';
import { INotificationOutboxWriter } from './notification-outbox.writer';
import { IRecipientResolver } from './recipient-resolver';

export interface PublishOptions {
  /** Set on a replica set: the events must be written in the caller's transaction. */
  session?: ClientSession;
  /** The actor, for recipient resolution that needs their identity (team expansion). */
  identity?: CallerIdentity;
}

/** The one place a collaboration mutation emits its events, after its audit row. */
export interface ICollaborationNotifier {
  /** With a session a failure must propagate so the mutation aborts. */
  publish(
    events: readonly CollaborationEvent[],
    opts?: PublishOptions,
  ): Promise<void>;
}

const viaTeam = (event: CollaborationEvent): boolean =>
  'principal' in event && event.principal.type !== 'user';

/** A user both direct and in a shared team keeps the direct row: its access level and its email intent. */
function directSharesFirst(
  events: readonly CollaborationEvent[],
): Array<[number, CollaborationEvent]> {
  const entries = [...events.entries()];
  return [
    ...entries.filter(([, event]) => !viaTeam(event)),
    ...entries.filter(([, event]) => viaTeam(event)),
  ];
}

export interface OutboxCollaborationNotifierDeps {
  recipients: IRecipientResolver;
  outbox: INotificationOutboxWriter;
  archiver: INotificationArchiver;
}

/**
 * Turns events into one outbox row per event (the transferred event is two: the new owner's
 * carries the email intent), written in the caller's session so a failure aborts the mutation.
 * A user reached twice by one change, directly and through a team, is told once.
 */
export class OutboxCollaborationNotifier implements ICollaborationNotifier {
  constructor(private readonly deps: OutboxCollaborationNotifierDeps) {}

  async publish(
    events: readonly CollaborationEvent[],
    opts: PublishOptions = {},
  ): Promise<void> {
    if (events.length === 0) {
      return;
    }
    const writeOpts = opts.session ? { session: opts.session } : {};
    const resolved = await Promise.all(
      events.map((event) => this.recipientsOf(event, opts)),
    );
    const told = new Map<CollaborationEventType, Set<string>>();
    const rows: NotificationBrokerMessage[] = [];
    for (const [index, event] of directSharesFirst(events)) {
      const recipients = resolved[index] ?? [];
      if (event.type === 'chat.unshared') {
        await this.deps.archiver.archiveShared(
          {
            orgId: event.orgId,
            sessionId: event.sessionId,
            userIds: recipients,
          },
          writeOpts,
        );
        continue;
      }
      const seen = told.get(event.type) ?? new Set<string>();
      told.set(event.type, seen);
      const fresh = recipients.filter((id) => !seen.has(id));
      fresh.forEach((id) => seen.add(id));
      if (fresh.length === 0) {
        continue;
      }
      if (event.type === 'chat.mentioned') {
        rows.push(
          ...fresh.map((id) =>
            toNotificationMessage(event, [id], event.emailIntents?.get(id)),
          ),
        );
        continue;
      }
      if (event.type === 'chat.ownershipTransferred') {
        rows.push(
          ...fresh.map((id) =>
            toNotificationMessage(
              event,
              [id],
              id === event.newOwnerUserId ? event.emailIntent : undefined,
            ),
          ),
        );
        continue;
      }
      rows.push(
        toNotificationMessage(
          event,
          fresh,
          event.type === 'chat.shared' ? event.emailIntent : undefined,
        ),
      );
    }
    await this.deps.outbox.write(rows, events[0]?.sessionId ?? '', writeOpts);
  }

  private async recipientsOf(
    event: CollaborationEvent,
    opts: PublishOptions,
  ): Promise<readonly string[]> {
    const resolve = (
      principals: Parameters<IRecipientResolver['resolve']>[0]['principals'],
      exclude?: ReadonlySet<string>,
    ): Promise<readonly string[]> =>
      this.deps.recipients.resolve({
        orgId: event.orgId,
        ...(opts.identity && { identity: opts.identity }),
        principals,
        ...(exclude && { exclude }),
      });
    const user = (userId: string): Principal => ({ type: 'user', userId });
    const actor = new Set([event.actorUserId]);
    switch (event.type) {
      case 'chat.shared':
      case 'chat.accessChanged':
        return resolve([event.principal], actor);
      case 'chat.unshared':
        return resolve([event.principal]);
      case 'chat.ownershipTransferred':
        return resolve([
          user(event.newOwnerUserId),
          user(event.previousOwnerUserId),
        ]);
      case 'chat.mentioned':
        return resolve(event.principals, actor);
      case 'chat.deleted':
      case 'chat.activity':
        return resolve(event.recipientUserIds.map(user), actor);
    }
  }
}
