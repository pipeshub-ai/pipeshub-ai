import { MongoSlackThreadRepository } from './mongo';
import type { SlackThreadRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';
export { connect, dropLegacyThreadBotIndex } from './mongo';

let repo: SlackThreadRepository | undefined;

export const getSlackThreadRepository = (): SlackThreadRepository =>
  (repo ??= pickStore<SlackThreadRepository>('slack-thread', {
    mongo: () => new MongoSlackThreadRepository(),
  }));

/** Replaces the repository (tests, container wiring); no argument restores the Mongo one. */
export const setSlackThreadRepository = (r?: SlackThreadRepository): void => {
  repo = r;
};
