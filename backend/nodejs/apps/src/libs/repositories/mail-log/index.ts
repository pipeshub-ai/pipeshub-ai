import { MongoMailLogRepository } from './mongo';
import type { MailLogRepository } from './types';

export type * from './types';

let repo: MailLogRepository | undefined;

export const getMailLogRepository = (): MailLogRepository =>
  (repo ??= new MongoMailLogRepository());

/** Tests: install a fake, or call with no argument to restore the default. */
export const setMailLogRepository = (r?: MailLogRepository): void => {
  repo = r;
};
