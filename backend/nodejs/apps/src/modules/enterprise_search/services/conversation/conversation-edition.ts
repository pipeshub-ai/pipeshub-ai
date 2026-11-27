import { Express } from 'express';
import { Container } from 'inversify';
import { Logger } from '../../../../libs/services/logger.service';
import { shadowEnabled } from '../../../../libs/repositories/shadow';
import { docStore } from '../../../../libs/repositories/store-select';
import { AppConfig } from '../../../tokens_manager/config/config';

/** The containers the conversation edition may bind into. */
export interface ConversationEditionContainers {
  enterpriseSearch: Container;
  projects: Container;
  knowledgeBase: Container;
  entityManager: Container;
  notification: Container;
}

/**
 * How the application wires conversations: which services back the guards and turn handlers and
 * which extra routes exist. Chosen at build time through `src/config.ts`.
 */
export interface ConversationEdition {
  /** Response headers browsers may read, besides the application's own. */
  readonly exposedHeaders: readonly string[];
  /** Once at startup, after the containers exist and before any router is built. */
  bind(
    containers: ConversationEditionContainers,
    appConfig: AppConfig,
    logger: Logger,
  ): void;
  /** Routes mounted on the application beside the conversation routers. */
  mountRoutes(app: Express, containers: ConversationEditionContainers): void;
}

/** What an operator is told when the document store is one chats cannot run on. */
export const CHATS_NEED_MONGO =
  'Chats need MongoDB storage in this version. Set DOC_STORE=mongo. ' +
  'See "Document store on PostgreSQL" in deployment/docker-compose/postgres/README.md.';

/**
 * Chats in this version read and write MongoDB themselves, besides the repositories `DOC_STORE` selects. On
 * `DOC_STORE=postgres` the application would start healthy while every chat waits on a MongoDB that is not there, even
 * with `DOC_STORE_MONGO_REPOS` keeping repositories on MongoDB; on `shadow` their reads would go uncompared. So it
 * refuses to start on either.
 */
export function assertChatsDocStore(): void {
  if (shadowEnabled() || docStore() !== 'mongo')
    throw new Error(CHATS_NEED_MONGO);
}

/** Conversations as the containers bind them by default, with no extra routes. Refuses a document store chats can't use. */
export const defaultConversationEdition: ConversationEdition = {
  exposedHeaders: [],
  bind: () => assertChatsDocStore(),
  mountRoutes: () => undefined,
};
