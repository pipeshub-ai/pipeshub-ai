import express, { Express, NextFunction, Request, Response } from 'express';
import { Container } from 'inversify';
import { HttpError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { ErrorMiddleware } from '../../../libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../libs/middlewares/request.context';
import {
  AGENT_HARNESS_INTERNAL_PREFIX,
  createAgentHarnessInternalRouter,
} from './internal-routes';
import { countTokenRefusals } from './internal-auth';
import { recordTokenRejection } from '../adapters/telemetry/token-rejection-metrics';
import { HttpReadLimits, positiveIntegerSetting } from './read-limits';
import { agentHarnessSwitchedOn } from '../config';

/**
 * The worker-only routes are served on a listener of their own (C-26), which no
 * shipped deployment publishes: the browser-facing port never serves them, so
 * reaching them takes the deployment's network as well as the worker credential.
 */
export const INTERNAL_PORT_ENV = 'AGENT_HARNESS_INTERNAL_PORT';
export const INTERNAL_HOST_ENV = 'AGENT_HARNESS_INTERNAL_HOST';
export const DEFAULT_INTERNAL_PORT = 3002;
export const DEFAULT_INTERNAL_HOST = '0.0.0.0';
export const INTERNAL_LISTENER_CODE = 'AGENT_HARNESS_INTERNAL_LISTENER';
export const INTERNAL_HEADERS_TIMEOUT_ENV =
  'AGENT_HARNESS_INTERNAL_HEADERS_TIMEOUT_MS';
export const INTERNAL_REQUEST_TIMEOUT_ENV =
  'AGENT_HARNESS_INTERNAL_REQUEST_TIMEOUT_MS';
export const INTERNAL_MAX_CONNECTIONS_ENV =
  'AGENT_HARNESS_INTERNAL_MAX_CONNECTIONS';
/**
 * The worker's requests are small JSON (10 MB at most) from inside the
 * deployment, so the request bounds are far tighter than the public listener's
 * (SEC-DOS-04). Keep-alive matches the public listener: the same Python pools
 * reuse connections to both.
 */
export const DEFAULT_INTERNAL_READ_LIMITS = {
  headersTimeoutMs: 15_000,
  requestTimeoutMs: 60_000,
  keepAliveTimeoutMs: 65_000,
  checkIntervalMs: 1_000,
  maxConnections: 1_024,
} satisfies HttpReadLimits;

export interface InternalListenerConfig {
  host: string;
  port: number;
  readLimits: HttpReadLimits;
}

/** Strict, like every harness setting (F3): a malformed port stops the process and names the setting. */
export function internalListenerConfig(
  env: NodeJS.ProcessEnv = process.env,
): InternalListenerConfig {
  const rawPort = env[INTERNAL_PORT_ENV]?.trim();
  const port =
    rawPort === undefined || rawPort === ''
      ? DEFAULT_INTERNAL_PORT
      : Number(rawPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(
      `${INTERNAL_PORT_ENV} must be a TCP port (1-65535), got ${JSON.stringify(rawPort)}`,
    );
  }
  // A process told to listen on one address (PIPESHUB_BIND_HOST: the native bundle's loopback) opens this port there too.
  const host =
    env[INTERNAL_HOST_ENV]?.trim() ||
    env.PIPESHUB_BIND_HOST?.trim() ||
    DEFAULT_INTERNAL_HOST;
  const defaults = DEFAULT_INTERNAL_READ_LIMITS;
  const readLimits: HttpReadLimits = {
    ...defaults,
    headersTimeoutMs: positiveIntegerSetting(
      INTERNAL_HEADERS_TIMEOUT_ENV,
      env[INTERNAL_HEADERS_TIMEOUT_ENV],
      defaults.headersTimeoutMs,
    ),
    requestTimeoutMs: positiveIntegerSetting(
      INTERNAL_REQUEST_TIMEOUT_ENV,
      env[INTERNAL_REQUEST_TIMEOUT_ENV],
      defaults.requestTimeoutMs,
    ),
    maxConnections: positiveIntegerSetting(
      INTERNAL_MAX_CONNECTIONS_ENV,
      env[INTERNAL_MAX_CONNECTIONS_ENV],
      defaults.maxConnections,
    ),
  };
  if (readLimits.headersTimeoutMs > readLimits.requestTimeoutMs) {
    throw new Error(
      `${INTERNAL_HEADERS_TIMEOUT_ENV} cannot be longer than ${INTERNAL_REQUEST_TIMEOUT_ENV}`,
    );
  }
  return { host, port, readLimits };
}

/**
 * Whether this process opens the internal listener at all (R50OFF-FU-5): its readers are the harness's worker and
 * gateways, and, for classic `run_code` in broker mode, the sandbox broker and the egress proxy. With the harness off
 * and code execution not on the broker nothing reads it, so no port is opened, as on a release without the harness.
 */
export function internalListenerWanted(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  return (
    agentHarnessSwitchedOn(env) ||
    (env.SANDBOX_MODE ?? '').trim().toLowerCase() === 'broker'
  );
}

/** The app the internal listener serves: the internal router and nothing else. */
export function createAgentHarnessInternalApp(container: Container): Express {
  const app = express();
  app.disable('x-powered-by');
  app.use(requestContextMiddleware);
  // SEC-C-09: no body is parsed here; each route parses its own after its guard (internal-auth `thenJson`).
  app.use(
    AGENT_HARNESS_INTERNAL_PREFIX,
    createAgentHarnessInternalRouter(container),
  );
  app.use((_req: Request, _res: Response, next: NextFunction) => {
    next(new HttpError('NOT_FOUND', 'Not found', HTTP_STATUS.NOT_FOUND));
  });
  app.use(countTokenRefusals(recordTokenRejection));
  app.use(ErrorMiddleware.handleError());
  return app;
}

/** Mounted at the internal prefix on the public app: says where the routes went instead of a bare 404. */
export function internalRoutesElsewhere() {
  return (_req: Request, _res: Response, next: NextFunction): void => {
    next(
      new HttpError(
        INTERNAL_LISTENER_CODE,
        `The agent harness's internal routes are served on ${INTERNAL_PORT_ENV} only`,
        HTTP_STATUS.NOT_FOUND,
      ),
    );
  };
}
