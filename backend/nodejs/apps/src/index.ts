import 'reflect-metadata';
import { config } from 'dotenv';
import { Logger } from './libs/services/logger.service';

// Loads environment variables
config();
import { Application } from './app';

const app = new Application();
const logger = Logger.getInstance();

let shuttingDown = false;

const gracefulShutdown = async (signal: string) => {
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
    Logger.getInstance().error('Error during shutdown:', error);
    process.exit(1);
  }
};

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
