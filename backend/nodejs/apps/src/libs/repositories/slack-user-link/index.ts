import { MongoSlackUserLinkRepository } from './mongo';
import { PgSlackUserLinkRepository } from './pg';
import type { SlackUserLinkRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: SlackUserLinkRepository | undefined;

export const getSlackUserLinkRepository = (): SlackUserLinkRepository =>
  (repo ??= pickStore<SlackUserLinkRepository>('slack-user-link', {
    postgres: () => new PgSlackUserLinkRepository(),
    mongo: () => new MongoSlackUserLinkRepository(),
  }));

/** Replaces the repository (tests); no argument restores the configured one. */
export const setSlackUserLinkRepository = (
  r?: SlackUserLinkRepository,
): void => {
  repo = r;
};
