import { ClientSession, Types } from 'mongoose';
import { Notifications } from '../../../../notification/schema/notification.schema';

export interface INotificationArchiver {
  /** Archives these users' earlier `chat.shared` notifications for a session they lost access to. */
  archiveShared(
    args: { orgId: string; sessionId: string; userIds: readonly string[] },
    opts?: { session?: ClientSession },
  ): Promise<void>;
}

export class MongoNotificationArchiver implements INotificationArchiver {
  async archiveShared(
    args: { orgId: string; sessionId: string; userIds: readonly string[] },
    opts: { session?: ClientSession } = {},
  ): Promise<void> {
    if (args.userIds.length === 0) {
      return;
    }
    await Notifications.updateMany(
      {
        orgId: new Types.ObjectId(args.orgId),
        assignedTo: { $in: args.userIds.map((id) => new Types.ObjectId(id)) },
        type: 'chat.shared',
        'payload.sessionId': args.sessionId,
        status: { $ne: 'archived' },
      },
      { $set: { status: 'archived' } },
      opts.session ? { session: opts.session } : {},
    );
  }
}
