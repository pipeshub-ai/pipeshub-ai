import { MailModel } from '../../../modules/mail/schema/mailInfo.schema';
import type { MailLogEntry, MailLogRepository } from './types';

export class MongoMailLogRepository implements MailLogRepository {
  async record(entry: MailLogEntry): Promise<void> {
    await new MailModel(entry).save();
  }
}
