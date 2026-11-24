import 'reflect-metadata';
import { config } from 'dotenv';
import { applyStoreUmbrella } from './libs/config/storeUmbrella';
import { checkStartup } from './edition';
import { Logger } from './libs/services/logger.service';
import axios from 'axios';
import axiosRetry from 'axios-retry';
import { installUpstreamLogging } from './libs/http/upstream-call';
import { RETRY_POLICY } from './libs/http/retry-policy';

// Loads environment variables
config({ quiet: true });
applyStoreUmbrella();
checkStartup();
// The imports above created the logger before .env was loaded.
Logger.applyEnv();
// Every default-instance axios call, each retry included, writes its upstream.call.end line.
installUpstreamLogging(axios);
axiosRetry(axios, RETRY_POLICY);
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
