import winston from 'winston';
import axios from 'axios';
import { Request } from 'express';
import { injectable } from 'inversify';
import path from 'path';
import { threadId } from 'worker_threads';
import {
  currentDisplayId,
  getRequestContext,
  LOG_CONTEXT_KEYS,
} from '../context/request-context';
import { BaseError } from '../errors/base.error';
import {
  axiosErrorForLog,
  redactHeadersForLog,
  redactLogMetadata,
  redactQueryForLog,
  redactSecretsInText,
} from '../utils/log-redaction.utils';
import { getProvider } from './structured-logging';

const redactMetadata = winston.format((info) => {
  if (info.metadata !== undefined) {
    info.metadata = redactLogMetadata(info.metadata);
  }
  return info;
});

export enum LogLevel {
  Debug = 'debug',
  Info = 'info',
  Warning = 'warning',
  Error = 'error',
}


const customLevels = {
  levels: {
    error: 0,
    warning: 1,
    info: 2,
    debug: 3,
  },
  colors: {
    error: 'red',
    warning: 'yellow',
    info: 'green',
    debug: 'green',
  },
};

winston.addColors(customLevels.colors);

const REDACTED = '[REDACTED]';

/**
 * Matched against the key with separators stripped, so `api-key`, `api_key`
 * and `apiKey` all hit. Numeric values are passed through, which keeps LLM
 * usage counters (maxTokens, totalTokens) readable.
 */
const SENSITIVE_KEY_FRAGMENTS = [
  'authorization',
  'cookie',
  'password',
  'passwd',
  'secret',
  'token',
  'apikey',
  'accesskey',
  'privatekey',
  'credential',
  'signature',
  'sessionid',
  'salt',
];

/** Decoded claims and scope lists are not credentials and stay useful in logs. */
const SAFE_KEY_EXCEPTIONS = new Set([
  'tokenpayload',
  'tokenscopes',
  'tokenusage',
]);

function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (SAFE_KEY_EXCEPTIONS.has(normalized)) {
    return false;
  }
  return SENSITIVE_KEY_FRAGMENTS.some((fragment) =>
    normalized.includes(fragment),
  );
}

export function getLogLevel(): LogLevel {
  const level = process.env.LOG_LEVEL?.toLowerCase();
  if (Object.values(LogLevel).includes(level as LogLevel)) {
    return level as LogLevel;
  }
  return LogLevel.Info;
}


export interface LoggerConfig {
  service: string;
  level?: string;
  additionalMeta?: Record<string, any>;
}

const DEFAULT_SERVICE = 'default-service';

/** Shown in the text line as ` | k=v`, in this order, when the record has them. */
const TEXT_FIELD_KEYS = ['org_id', 'user_id', ...LOG_CONTEXT_KEYS] as const;

/**
 * The text-line value rule shared with the Python side (docs/logging.md): bare
 * when it is only these characters, else a JSON string, so an id taken from a
 * URL can never start a new line or a fake field. The empty string is `""`.
 */
const BARE_TEXT_VALUE = /^[A-Za-z0-9._:@/-]+$/;

export function formatTextValue(value: string | number | boolean): string {
  const text = String(value);
  return BARE_TEXT_VALUE.test(text) ? text : JSON.stringify(text);
}

/** null and undefined are left out of the text line. */
function textFields(info: Record<string, unknown>): string {
  const pairs = TEXT_FIELD_KEYS.filter((key) => info[key] != null).map(
    (key) => `${key}=${formatTextValue(info[key] as string | number | boolean)}`,
  );
  return pairs.length > 0 ? ` | ${pairs.join(' ')}` : '';
}

/** Request id, thread, identity (as the provider shows it) and bound ids, or nothing outside a context. */
function logContextFields(): Record<string, unknown> {
  const store = getRequestContext();
  if (!store) return {};
  const fields: Record<string, unknown> = {
    request_id: currentDisplayId(),
    thread_id: threadId,
    ...store.logFields,
  };
  const provider = getProvider();
  if (provider.logIdentity) {
    const shown = provider.logIdentity({
      ...(store.orgId && { org_id: store.orgId }),
      ...(store.userId && { user_id: store.userId }),
    });
    if (shown.org_id) fields.org_id = shown.org_id;
    if (shown.user_id) fields.user_id = shown.user_id;
  } else if (store.orgId && provider.hashOrgId) {
    const orgHash = provider.hashOrgId(store.orgId);
    if (orgHash) fields.organization_id_hash = orgHash;
  }
  return fields;
}

/**
 * One `Logger` per service name, all sharing the root's winston logger and
 * transports. A named logger is `Object.create(root)`, the same technique as
 * winston's own `child()`, so it inherits every method: a stub on
 * `Logger.prototype` or on the root instance sees calls from every module.
 * Its own `defaultMeta` carries the service; the level stays process-wide.
 */
@injectable()
export class Logger {
  private static instance: Logger;
  private logger: winston.Logger = {} as winston.Logger;
  private defaultMeta: Record<string, any> = {};
  private children = new Map<string, Logger>();

  constructor(config?: LoggerConfig) {
    if (!Logger.instance) {
      this.defaultMeta = { service: DEFAULT_SERVICE };
      this.logger = this.initializeLogger(config);
      Logger.instance = this;
    }
    // Not through getInstance: tests stub that, and `new Logger` must keep working under the stub.
    return Logger.forService(config);
  }

  /** The root for a nameless call, else the one logger for `config.service`. */
  private static forService(config?: LoggerConfig): Logger {
    const root = Logger.instance;
    if (config?.level) {
      root.logger.level = config.level;
    }
    const logger =
      !config?.service || config.service === DEFAULT_SERVICE
        ? root
        : root.named(config.service);
    if (config?.additionalMeta) {
      logger.updateDefaultMeta(config.additionalMeta);
    }
    return logger;
  }

  private named(service: string): Logger {
    let logger = this.children.get(service);
    if (!logger) {
      logger = Object.create(this) as Logger;
      logger.defaultMeta = { service };
      this.children.set(service, logger);
    }
    return logger;
  }

  private initializeLogger(config?: LoggerConfig): winston.Logger {
    const logFormat = winston.format.combine(
      // First, so the console line and the JSON file lines both get it.
      redactMetadata(),
      winston.format.timestamp(),
      winston.format.errors({ stack: true }),
      winston.format.printf((info) => {
        const { message, metadata, stack, filename, line, request_id, thread_id } = info;
        const service = info.service || DEFAULT_SERVICE;

        const ts =
          typeof info.timestamp === 'string'
            ? info.timestamp.replace('T', ' ').replace('Z', '').replace('.', ',')
            : info.timestamp;

        const rawLevel = String(info[Symbol.for('level')] || info.level).toUpperCase();
        const levelColors: Record<string, string> = { ERROR: '\x1b[31m', WARNING: '\x1b[33m' };
        const levelStr = levelColors[rawLevel]
          ? `${levelColors[rawLevel]}${rawLevel}\x1b[0m`
          : rawLevel;

        // Already redacted and de-cycled by logWithLevel.
        let metaString = '';
        if (metadata) {
          try {
            metaString = `\nmetadata: ${JSON.stringify(metadata, null, 2)}`;
          } catch (error) {
            metaString = '\nmetadata: [Circular Structure]';
          }
        }
        const stackString = stack ? `\nstack: ${stack}` : '';
        const fileInfo = filename ? `[${filename}:${line}]` : '';
        // Present only when a request context is in flight.
        const traceInfo =
          request_id !== undefined ? `[req:${request_id} thr:${thread_id}] ` : '';

        return `${ts} - ${service} - ${levelStr} - ${traceInfo}${fileInfo} - ${message}${textFields(info)}${metaString}${stackString}`;
      })
    );

    const provider = getProvider();
    const transports: winston.transport[] = [];
    if (provider.fileLoggingEnabled()) {
      transports.push(
        new winston.transports.File({
          filename: 'error.log',
          level: LogLevel.Error,
          format: winston.format.combine(logFormat, winston.format.json())
        }),
        new winston.transports.File({
          filename: 'combined.log',
          format: winston.format.combine(logFormat, winston.format.json())
        }),
      );
    }
    transports.push(
      new winston.transports.Console({
        format: provider.consoleFormat(logFormat),
      }),
    );

    return winston.createLogger({
      levels: customLevels.levels,
      level: config?.level || getLogLevel(),
      format: logFormat,
      // Not `service`: winston's defaultMeta overrides the record, and each named logger sets its own.
      defaultMeta: { service_tier: 'nodejs_service' },
      transports,
    });
  }

  private sanitizeForLogging(obj: any, seen = new WeakSet()): any {
    // Handle non-object types
    if (obj === null || typeof obj !== 'object') {
      return obj;
    }

    // Handle circular references
    if (seen.has(obj)) {
      return '[Circular]';
    }
    seen.add(obj);

    // Handle arrays
    if (Array.isArray(obj)) {
      return obj.map(item => this.sanitizeForLogging(item, seen));
    }

    if (obj instanceof Error) {
      return this.serializeError(obj, seen);
    }
    // Dates and Mongo ids have no own keys worth walking; JSON shows them through toJSON.
    const { toJSON } = obj as { toJSON?: unknown };
    if (typeof toJSON === 'function') {
      return this.sanitizeForLogging((toJSON as () => unknown).call(obj), seen);
    }

    // Handle objects
    const sanitized: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj)) {
      // Skip certain properties that might cause circular references
      if (key === 'parent' || key === '_readableState' || key === 'pipes') {
        continue;
      }
      if (isSensitiveKey(key)) {
        sanitized[key] = typeof value === 'number' ? value : REDACTED;
        continue;
      }
      try {
        sanitized[key] = this.sanitizeForLogging(value, seen);
      } catch (error) {
        sanitized[key] = '[Unable to serialize]';
      }
    }
    return sanitized;
  }

  /**
   * What an error says about why it happened, never what it carried: an axios
   * error through the shared allowlist (no config, request or body); any other
   * error by name, code and message, plus status and metadata for our own
   * errors. `cause` is followed through the same walk, so a cycle prints
   * `[Circular]` (as does an object met twice).
   */
  private serializeError(
    error: Error,
    seen: WeakSet<object>,
  ): Record<string, unknown> {
    const { code, cause } = error as { code?: unknown; cause?: unknown };
    const out: Record<string, unknown> = axios.isAxiosError(error)
      ? axiosErrorForLog(error, true)
      : {
          name: error.name,
          ...((typeof code === 'string' || typeof code === 'number') && {
            code,
          }),
          ...(error instanceof BaseError && {
            statusCode: error.statusCode,
            metadata: this.sanitizeForLogging(error.metadata, seen),
          }),
          message:
            typeof error.message === 'string'
              ? redactSecretsInText(error.message)
              : undefined,
          stack:
            error.stack === undefined
              ? undefined
              : redactSecretsInText(error.stack),
        };
    if (cause !== undefined) {
      out.cause = this.sanitizeForLogging(cause, seen);
    }
    return out;
  }

  static getInstance(config?: LoggerConfig): Logger {
    if (!Logger.instance) {
      new Logger({ service: DEFAULT_SERVICE, level: getLogLevel() });
    }
    return Logger.forService(config);
  }

  /** Adds to this logger's own metadata only; its service is fixed by the name it was created with. */
  updateDefaultMeta(metadata: Record<string, any>): void {
    this.defaultMeta = {
      ...this.defaultMeta,
      ...metadata,
    };
  }

  private getCallerInfo(): { filename: string, line: number } {
    const error = new Error();
    Error.captureStackTrace(error);

    // Get the full stack trace
    const stackLines = error.stack?.split('\n') || [];

    // Find the first line that's not from the logger itself
    const callerLine = stackLines.find(line =>
      !line.includes('logger.service.ts') &&
      !line.includes('at Logger.') &&
      line.includes('at ')
    );

    let filename = 'unknown';
    let lineNumber = 0;
    if (callerLine) {
      // Extract filename from the last part of the path
      const regex = /at\s+(?:.+?\s+)?\(?(.+?):(\d+):(\d+)\)?/;
      const match = callerLine.match(regex);
      if (match && match[1]) {
        filename = path.basename(match[1]);
      }
      if (match && match[2]) {
        lineNumber = parseInt(match[2], 10);
      }
    }

    return { filename, line: lineNumber };
  }

  private logWithLevel(level: string, message: string, meta?: any) {
    // Bail before getCallerInfo(): it captures and symbolises a stack trace,
    // which profiling put at ~5% of gateway CPU -- and it was being paid in
    // full for debug lines that the configured level then discarded.
    // Guarded on the method existing: `logger` starts life as an empty cast
    // and is only replaced once initializeLogger has run.
    if (
      typeof this.logger.isLevelEnabled === 'function' &&
      !this.logger.isLevelEnabled(level)
    ) {
      return;
    }
    const callerInfo = this.getCallerInfo();
    // Redact here, not in the printf format: the file transports append
    // winston's json() after it, and json() re-serialises `info` from scratch,
    // so anything only cleaned inside printf still reaches the log file raw.
    this.logger.log({
      level,
      message,
      metadata: meta === undefined ? undefined : this.sanitizeForLogging(meta),
      ...this.defaultMeta,
      // Read at emit time; omitted entirely when there is no context.
      ...logContextFields(),
      ...callerInfo,
    });
  }

  error(message: string, meta?: any) {
    this.logWithLevel(LogLevel.Error, message, meta);
  }

  warn(message: string, meta?: any) {
    this.logWithLevel(LogLevel.Warning, message, meta);
  }

  info(message: string, meta?: any) {
    this.logWithLevel(LogLevel.Info, message, meta);
  }

  debug(message: string, meta?: any) {
    this.logWithLevel(LogLevel.Debug, message, meta);
  }

  logRequest(req: Request, meta?: any) {
    const callerInfo = this.getCallerInfo();
    this.info(`${req.method} ${req.path}`, {
      ...meta,
      ...callerInfo,
      query: redactQueryForLog(req.query),
      params: req.params,
      headers: redactHeadersForLog(req.headers),
    });
  }
}
