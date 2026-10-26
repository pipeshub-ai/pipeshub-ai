import { MongoConversationRepository } from './mongo';
import type { ConversationRepository } from './types';

export * from './types';

let repo: ConversationRepository | undefined;

export const getConversationRepository = (): ConversationRepository =>
  (repo ??= new MongoConversationRepository());

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setConversationRepository = (r?: ConversationRepository): void => {
  repo = r;
};
