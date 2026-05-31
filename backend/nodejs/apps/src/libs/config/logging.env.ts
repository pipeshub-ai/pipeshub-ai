/**
 * Environment-backed settings for logging.
 * Keep `process.env` reads here only — not in services or factories.
 */

import {
  DEFAULT_LOG_DIR,
  DEFAULT_LOG_LEVEL,
  ENV_LOG_DIR,
  ENV_LOG_LEVEL,
} from '../constants/logging.constants';

export interface LoggingEnv {
  logLevel: string;
  logDir: string;
}

export function loadLoggingEnv(): LoggingEnv {
  return {
    logLevel: process.env[ENV_LOG_LEVEL] ?? DEFAULT_LOG_LEVEL,
    logDir: process.env[ENV_LOG_DIR] ?? DEFAULT_LOG_DIR,
  };
}
