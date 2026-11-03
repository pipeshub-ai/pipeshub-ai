import type { Application } from './app';
import { Logger } from './libs/services/logger.service';

export const createGracefulShutdown = (
  app: Pick<Application, 'stop'>,
  logger: Logger,
): ((signal: string) => Promise<void>) => {
  let shuttingDown = false;
  return async (signal: string): Promise<void> => {
    // A second signal (or an error thrown while stopping) must not start a
    // second, overlapping teardown of the same containers.
    if (shuttingDown) {
      logger.warn(`Received ${signal} while already shutting down; ignoring`);
      return;
    }
    shuttingDown = true;
    logger.info(`Received ${signal}. Starting graceful shutdown...`);
    try {
      await app.stop();
      process.exit(0);
    } catch (error) {
      logger.error('Error during shutdown:', error);
      process.exit(1);
    }
  };
};
