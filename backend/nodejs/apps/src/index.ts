import 'reflect-metadata';
import { config } from 'dotenv';
import { applyStoreUmbrella } from './libs/config/storeUmbrella';
import { checkStartup } from './edition';
import { Logger } from './libs/services/logger.service';
import {
  processShutdown,
  SHUTDOWN_DEADLINE_MS,
} from './libs/services/process-shutdown';
import { validateEnvFlags } from './libs/utils/env-flags';
import { validateAgentHarnessEnvironment } from './modules/agent-harness/config';
import { enforceStoreAuth, StoreAuthRefused } from './libs/config/store-auth';
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
// A malformed harness flag stops the process here instead of reading as its default.
validateEnvFlags();
validateAgentHarnessEnvironment();
import { Application } from './app';

const app = new Application();
const logger = Logger.getInstance();

const shutdown = processShutdown({
  stop: () => app.stop(),
  logger,
  exit: (code) => process.exit(code),
  deadlineMs: SHUTDOWN_DEADLINE_MS,
});

process.on('uncaughtException', (error) => {
  shutdown.fault('uncaughtException', error);
});

process.on('unhandledRejection', (reason, promise) => {
  logger.error('Unhandled Rejection:', {
    reason: reason instanceof Error ? {
      name: reason.name,
      message: reason.message,
    } : String(reason),
    promise: promise.toString()
  });
});

process.on('SIGTERM', () => shutdown.signal('SIGTERM'));
process.on('SIGINT', () => shutdown.signal('SIGINT'));

(async () => {
  try {
    await enforceStoreAuth(process.env, logger);
    await app.preInitMigration();
    await app.initialize();
    await app.start();
    await app.runMigration();
  } catch (error) {
    // The refusal is the operator's instructions: log it as text, not as an error object's fields.
    if (error instanceof StoreAuthRefused) logger.error(error.message);
    else logger.error('Failed to start application:', error);
    process.exit(1);
  }
})();
