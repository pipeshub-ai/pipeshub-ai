import { Express } from 'express';
import { Container } from 'inversify';
import { Logger } from '../../../../libs/services/logger.service';
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

/** Conversations as the containers bind them by default, with no extra routes. */
export const defaultConversationEdition: ConversationEdition = {
  exposedHeaders: [],
  bind: () => undefined,
  mountRoutes: () => undefined,
};
