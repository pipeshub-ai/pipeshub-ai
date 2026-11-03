import { MongoNotificationRepository } from './mongo';
import { PgNotificationRepository } from './pg';
import { pickStore } from '../store-select';
import type { NotificationRepository } from './types';

export type * from './types';

let repo: NotificationRepository | undefined;

export const getNotificationRepository = (): NotificationRepository =>
  (repo ??= pickStore<NotificationRepository>('notification', {
    postgres: () => new PgNotificationRepository(),
    mongo: () => new MongoNotificationRepository(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setNotificationRepository = (r?: NotificationRepository): void => {
  repo = r;
};
