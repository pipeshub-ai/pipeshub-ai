import { MongoConversationRepository } from './mongo';
import { PgConversationRepository } from './pg';
import { pickStore } from '../store-select';
import type { ConversationRepository } from './types';

export * from './types';

let repo: ConversationRepository | undefined;

export const getConversationRepository = (): ConversationRepository =>
  (repo ??= pickStore<ConversationRepository>('conversation', {
    postgres: () => new PgConversationRepository(),
    mongo: () => new MongoConversationRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setConversationRepository = (r?: ConversationRepository): void => {
  repo = r;
};
