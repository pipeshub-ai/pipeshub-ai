import 'reflect-metadata';
import { config } from 'dotenv';
import { applyStoreUmbrella } from './libs/config/storeUmbrella';
import { Logger } from './libs/services/logger.service';

// Loads environment variables
config({ quiet: true });
applyStoreUmbrella();
import { Application } from './app';
import { createGracefulShutdown } from './shutdown';

const app = new Application();
const logger = Logger.getInstance();

const gracefulShutdown = createGracefulShutdown(app, logger);

// Global error handlers to prevent app crashes
process.on('uncaughtException', (error) => {
  logger.error('Uncaught Exception:', {
    error: {
      name: error.name,
      message: error.message,
    }
  });
  // Process state is undefined after an uncaught exception, so drain and
  // exit; the process monitor restarts Node.
  gracefulShutdown('uncaughtException');
});

process.on('unhandledRejection', (reason, promise) => {
  logger.error('Unhandled Rejection:', {
    reason: reason instanceof Error ? {
      name: reason.name,
      message: reason.message,
    } : String(reason),
    promise: promise.toString()
  });
  // let the app try to recover for now until we have a better solution: to restart the app in a new process
  // gracefulShutdown('unhandledRejection'); TODO: add this once we have a better solution
});

process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
process.on('SIGINT', () => gracefulShutdown('SIGINT'));

(async () => {
  try {
    await app.preInitMigration();
    await app.initialize();
    await app.start();
    await app.runMigration();
  } catch (error) {
    logger.error('Failed to start application:', error);
    process.exit(1);
  }
})();
