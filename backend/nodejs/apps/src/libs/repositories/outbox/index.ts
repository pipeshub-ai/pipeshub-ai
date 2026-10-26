import { MongoOutboxRepository } from './mongo';
import type { OutboxRepository } from './types';

export type * from './types';

let repo: OutboxRepository | undefined;

export const getOutboxRepository = (): OutboxRepository =>
  (repo ??= new MongoOutboxRepository());

/** Tests: install a fake, or call with no argument to restore the default. */
export const setOutboxRepository = (r?: OutboxRepository): void => {
  repo = r;
};
