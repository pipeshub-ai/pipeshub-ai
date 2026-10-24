import type { Express } from 'express';
import type { Container } from 'inversify';
import type { Logger } from '../services/logger.service';
import type { TopicDefinition } from '../types/messaging.types';
import type { AppConfig } from '../../modules/tokens_manager/config/config';
import type { ConfigurationManagerConfig } from '../../modules/configuration_manager/config/config';

/** Extra Content-Security-Policy sources an edition's pages load from. */
export interface EditionCspSources {
  connectSrc: string[];
  scriptSrc: string[];
  frameSrc: string[];
}

/** Containers an edition's boot hooks may need; any can be missing. */
export interface EditionContainers {
  entityManager?: Container;
  auth?: Container;
}

/**
 * Start-up and shutdown hooks an edition can supply through config.ts.
 * app.ts calls each at a fixed point; this build supplies noopEditionBoot.
 */
export interface EditionBoot {
  /** Extra broker topics created with the required ones. */
  topics: TopicDefinition[];
  /** Added to the built-in lists when STRICT_MODE turns the CSP on. */
  cspSources: EditionCspSources;
  initialize(
    configurationManagerConfig: ConfigurationManagerConfig,
    appConfig: AppConfig,
  ): Promise<void>;
  /** Routes that read the raw request body (signed webhooks); mounted before body parsing. */
  mountRawRoutes(app: Express): void;
  mountRoutes(app: Express, containers: EditionContainers): void;
  startWorkers(logger: Logger): void;
  /** After the HTTP server listens. */
  afterListen(logger: Logger, containers: EditionContainers): Promise<void>;
  stop(logger: Logger, containers: EditionContainers): Promise<void>;
  dispose(): Promise<void>;
}

export const noopEditionBoot: EditionBoot = {
  topics: [],
  cspSources: { connectSrc: [], scriptSrc: [], frameSrc: [] },
  initialize: async () => {},
  mountRawRoutes: () => {},
  mountRoutes: () => {},
  startWorkers: () => {},
  afterListen: async () => {},
  stop: async () => {},
  dispose: async () => {},
};
