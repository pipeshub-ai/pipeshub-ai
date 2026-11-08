import { MongoOutboxRepository } from './mongo';
import { PgOutboxRepository } from './pg';
import { pickStore } from '../store-select';
import type { OutboxRepository } from './types';

export type * from './types';

let repo: OutboxRepository | undefined;

export const getOutboxRepository = (): OutboxRepository =>
  (repo ??= pickStore<OutboxRepository>('outbox', {
    postgres: () => new PgOutboxRepository(),
    mongo: () => new MongoOutboxRepository(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setOutboxRepository = (r?: OutboxRepository): void => {
  repo = r;
};
