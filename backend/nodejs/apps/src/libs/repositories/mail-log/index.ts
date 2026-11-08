import { PgMailLogRepository } from './pg';
import { MongoMailLogRepository } from './mongo';
import { pickStore } from '../store-select';
import type { MailLogRepository } from './types';

export type * from './types';

let repo: MailLogRepository | undefined;

export const getMailLogRepository = (): MailLogRepository =>
  (repo ??= pickStore('mail-log', {
    postgres: () => new PgMailLogRepository(),
    mongo: () => new MongoMailLogRepository(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setMailLogRepository = (r?: MailLogRepository): void => {
  repo = r;
};
