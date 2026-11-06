import winston, { Logform } from 'winston';
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

/** audit() logs on behalf of its caller, so its own frame is skipped like the logger's. */
const AUDIT_FRAME = /[\\/]libs[\\/]services[\\/]audit\.[jt]s:/;

/** Event fields a call site may pass in `meta`; they render like the context ids. */
const EVENT_FIELD_KEYS = [
  'method',
  'route',
  'status',
  'duration_ms',
  'client_ip',
  'error_class',
  'error_type',
  'outcome',
  'actor',
  'target',
  'reason',
] as const;

/** Shown in the text line as ` | k=v`, in this order (identity first, as in Python), when the record has them. */
const TEXT_FIELD_KEYS = [
  'org_id',
  'user_id',
  ...LOG_CONTEXT_KEYS,
  ...EVENT_FIELD_KEYS,
] as const;

/** The top-level JSON keys after `request_id`, in order. */
const JSON_FIELD_KEYS = [
  'org_id',
  'user_id',
  'organization_id_hash',
  'thread_id',
  ...LOG_CONTEXT_KEYS,
  ...EVENT_FIELD_KEYS,
] as const;

const HTTP_METHODS = new Set([
  'GET',
  'HEAD',
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
  'OPTIONS',
]);
const OUTCOMES = new Set(['success', 'failure', 'partial', 'skipped']);
/** A snake_case code (`error_class`, an audit `reason`). */
export const SNAKE_TOKEN = /^[a-z0-9_]{1,64}$/;
const TYPE_NAME = /^[A-Za-z0-9_]{1,64}$/;
const SHOWN_ID = /^[A-Za-z0-9._:-]{1,128}$/;
/** A dotted lowercase event name (`http.request.end`). */
export const EVENT_NAME = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/;
/** The ids PipesHub issues: Mongo ObjectId, UUID, or a `sys-`/`anon-` root. */
const PIPESHUB_ID =
  /^(?:[a-f0-9]{24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?:sys|anon)-[A-Za-z0-9]{1,64})$/;

export const isPipesHubId = (value: unknown): value is string =>
  typeof value === 'string' && PIPESHUB_ID.test(value);

const isText = (v: unknown): boolean => typeof v === 'string';
const isIdValue = (v: unknown): boolean =>
  typeof v === 'string' || typeof v === 'number';
const matches =
  (pattern: RegExp) =>
  (v: unknown): boolean =>
    typeof v === 'string' && pattern.test(v);

/**
 * A `meta` key becomes a field (` | k=v`, a JSON key) only when its value has
 * the field's type; anything else stays in `metadata`. So `status` is always
 * the HTTP status in both runtimes, and free text never reaches the suffix.
 * `null` is kept (left out of the text, `null` in JSON).
 */
const FIELD_TYPES: Readonly<Record<string, (value: unknown) => boolean>> = {
  ...Object.fromEntries(LOG_CONTEXT_KEYS.map((key) => [key, isIdValue])),
  method: (v) => typeof v === 'string' && HTTP_METHODS.has(v),
  route: isText,
  status: Number.isInteger,
  duration_ms: Number.isInteger,
  client_ip: isText,
  error_class: matches(SNAKE_TOKEN),
  error_type: matches(TYPE_NAME),
  outcome: (v) => typeof v === 'string' && OUTCOMES.has(v),
  // An id as shown (raw, or a provider's hash); never an email or a name.
  actor: matches(SHOWN_ID),
  target: matches(SHOWN_ID),
  reason: matches(SNAKE_TOKEN),
  // Fill the identity only when the request context has none (an audit line before sign-in).
  org_id: isPipesHubId,
  user_id: isPipesHubId,
  // Reserved: on the record for a provider (EE reads it), not printed by the default formats.
  event_name: matches(EVENT_NAME),
};

const isPlainObject = (value: unknown): value is Record<string, unknown> => {
  if (value === null || typeof value !== 'object') return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
};

/** Splits a call site's `meta` into typed fields and the rest, which stays `metadata`. */
function splitFields(meta: unknown): {
  fields: Record<string, unknown>;
  rest: unknown;
} {
  if (!isPlainObject(meta)) return { fields: {}, rest: meta };
  const fields: Record<string, unknown> = {};
  const rest: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(meta)) {
    // Own keys only: `valueOf` or `constructor` in a payload must not reach Object.prototype.
    const fits = Object.prototype.hasOwnProperty.call(FIELD_TYPES, key)
      ? FIELD_TYPES[key]
      : undefined;
    if (fits && value === undefined) continue;
    if (fits && (value === null || fits(value))) fields[key] = value;
    else rest[key] = value;
  }
  const lifted = Object.keys(fields).length > 0;
  return {
    fields,
    rest:
      lifted && Object.keys(rest).length === 0
        ? undefined
        : lifted
          ? rest
          : meta,
  };
}

/**
 * How an org and a user id appear on a record, as the provider decides: with
 * `logIdentity`, what it returns; without it, the legacy `hashOrgId` path
 * (`organization_id_hash`, never a raw id). Used for the request's identity
 * and for any other id that names a user (an audit actor).
 */
export function shownIdentity(ids: { org_id?: string; user_id?: string }): {
  org_id?: string;
  user_id?: string;
  organization_id_hash?: string;
} {
  const provider = getProvider();
  if (provider.logIdentity) {
    const shown = provider.logIdentity({
      ...(ids.org_id && { org_id: ids.org_id }),
      ...(ids.user_id && { user_id: ids.user_id }),
    });
    return {
      ...(shown.org_id && { org_id: shown.org_id }),
      ...(shown.user_id && { user_id: shown.user_id }),
    };
  }
  const orgHash =
    ids.org_id && provider.hashOrgId ? provider.hashOrgId(ids.org_id) : null;
  return orgHash ? { organization_id_hash: orgHash } : {};
}

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
    (key) =>
      `${key}=${formatTextValue(info[key] as string | number | boolean)}`,
  );
  return pairs.length > 0 ? ` | ${pairs.join(' ')}` : '';
}

/**
 * Request id, thread, bound ids, then the call's own fields (an explicit field
 * beats a bound one, as in Python), then the identity as the provider shows it.
 * An explicit org_id/user_id only fills an identity the context does not have.
 */
function recordFields(
  fields: Record<string, unknown>,
): Record<string, unknown> {
  const { org_id: orgId, user_id: userId, ...own } = fields;
  const store = getRequestContext();
  const identity = shownIdentity({
    org_id: store?.orgId ?? (orgId as string | undefined) ?? undefined,
    user_id: store?.userId ?? (userId as string | undefined) ?? undefined,
  });
  if (!store) return { ...own, ...identity };
  return {
    request_id: currentDisplayId(),
    thread_id: threadId,
    ...store.logFields,
    ...own,
    ...identity,
  };
}

const MESSAGE = Symbol.for('message');

/** Every string, at any depth, through the text redactor (the JSON line's rule, as in Python). */
function redactStrings(value: unknown): unknown {
  if (typeof value === 'string') return redactSecretsInText(value);
  if (Array.isArray(value)) return value.map(redactStrings);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, v]) => [key, redactStrings(v)]),
    );
  }
  return value;
}

/** What the serialiser made of an Error: a name and a message (see serializeError). */
const isSerializedError = (value: unknown): value is Record<string, unknown> =>
  isPlainObject(value) && typeof value.name === 'string' && 'message' in value;

/**
 * One JSON object per line with the Python keys (docs/logging.md): timestamp,
 * level, logger, filename, line, message, request_id, identity, the other
 * fields, `error` (a serialised Error), everything else under `metadata`, stack.
 */
const jsonLine = winston.format((info) => {
  const out: Record<string, unknown> = {
    timestamp: info.timestamp,
    level: info.level,
    logger: info.service || DEFAULT_SERVICE,
    filename: info.filename,
    line: info.line,
    message: info.message,
    request_id: info.request_id ?? null,
  };
  for (const key of JSON_FIELD_KEYS) {
    if (info[key] !== undefined) out[key] = info[key];
  }
  const { metadata } = info;
  if (isSerializedError(metadata)) {
    out.error = metadata;
  } else if (isPlainObject(metadata) && isSerializedError(metadata.error)) {
    const { error, ...rest } = metadata;
    out.error = error;
    if (Object.keys(rest).length > 0) out.metadata = rest;
  } else if (metadata !== undefined) {
    out.metadata = metadata;
  }
  if (info.stack !== undefined) out.stack = info.stack;
  info[MESSAGE] = JSON.stringify(redactStrings(out), (_key, value: unknown) =>
    typeof value === 'bigint' ? value.toString() : value,
  );
  return info;
});

const LOG_FORMATS = ['text', 'json'] as const;
export type LogFormat = (typeof LOG_FORMATS)[number];

/** `LOG_FORMAT`: `text` (default) or `json`, any case; anything else is text. */
export function getLogFormat(): LogFormat {
  return process.env.LOG_FORMAT?.trim().toLowerCase() === 'json'
    ? 'json'
    : 'text';
}

const textFormat = winston.format.combine(
  // First, so the console line and the JSON file lines both get it.
  redactMetadata(),
  winston.format.timestamp(),
  winston.format.errors({ stack: true }),
  winston.format.printf((info) => {
    const { message, metadata, stack, filename, line, request_id, thread_id } =
      info;
    const service = info.service || DEFAULT_SERVICE;

    const ts =
      typeof info.timestamp === 'string'
        ? info.timestamp.replace('T', ' ').replace('Z', '').replace('.', ',')
        : info.timestamp;

    const rawLevel = String(
      info[Symbol.for('level')] || info.level,
    ).toUpperCase();
    const levelColors: Record<string, string> = {
      ERROR: '\x1b[31m',
      WARNING: '\x1b[33m',
    };
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
  }),
);

/** The console format for the current LOG_FORMAT, offered to the provider as its default. */
function consoleFormat(): Logform.Format {
  return getProvider().consoleFormat(
    getLogFormat() === 'json'
      ? winston.format.combine(textFormat, jsonLine())
      : textFormat,
  );
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
    const provider = getProvider();
    const transports: winston.transport[] = [];
    if (provider.fileLoggingEnabled()) {
      transports.push(
        new winston.transports.File({
          filename: 'error.log',
          level: LogLevel.Error,
          format: winston.format.combine(textFormat, winston.format.json()),
        }),
        new winston.transports.File({
          filename: 'combined.log',
          format: winston.format.combine(textFormat, winston.format.json()),
        }),
      );
    }
    transports.push(
      new winston.transports.Console({ format: consoleFormat() }),
    );

    return winston.createLogger({
      levels: customLevels.levels,
      level: config?.level || getLogLevel(),
      format: textFormat,
      // Not `service`: winston's defaultMeta overrides the record, and each named logger sets its own.
      defaultMeta: { service_tier: 'nodejs_service' },
      transports,
    });
  }

  /** `withStack`: stacks are kept only on ERROR lines (a WARN says why without where, as in Python). */
  private sanitizeForLogging(
    obj: any,
    seen = new WeakSet(),
    withStack = true,
  ): any {
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
      return obj.map((item) => this.sanitizeForLogging(item, seen, withStack));
    }

    if (obj instanceof Error) {
      return this.serializeError(obj, seen, withStack);
    }
    // Dates and Mongo ids have no own keys worth walking; JSON shows them through toJSON.
    const { toJSON } = obj as { toJSON?: unknown };
    if (typeof toJSON === 'function') {
      return this.sanitizeForLogging(
        (toJSON as () => unknown).call(obj),
        seen,
        withStack,
      );
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
        sanitized[key] = this.sanitizeForLogging(value, seen, withStack);
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
    withStack: boolean,
  ): Record<string, unknown> {
    const { code, cause, status } = error as {
      code?: unknown;
      cause?: unknown;
      status?: unknown;
    };
    const out: Record<string, unknown> = axios.isAxiosError(error)
      ? axiosErrorForLog(error, withStack)
      : {
          name: error.name,
          ...((typeof code === 'string' || typeof code === 'number') && {
            code,
          }),
          ...(error instanceof BaseError
            ? {
                statusCode: error.statusCode,
                metadata: this.sanitizeForLogging(
                  error.metadata,
                  seen,
                  withStack,
                ),
              }
            : Number.isInteger(status) && { status }),
          message:
            typeof error.message === 'string'
              ? redactSecretsInText(error.message)
              : undefined,
          ...(withStack &&
            error.stack !== undefined && {
              stack: redactSecretsInText(error.stack),
            }),
        };
    if (cause !== undefined) {
      out.cause = this.sanitizeForLogging(cause, seen, withStack);
    }
    return out;
  }

  static getInstance(config?: LoggerConfig): Logger {
    if (!Logger.instance) {
      new Logger({ service: DEFAULT_SERVICE, level: getLogLevel() });
    }
    return Logger.forService(config);
  }

  /**
   * Re-reads LOG_LEVEL and LOG_FORMAT. index.ts calls it once dotenv has loaded
   * `.env`: the imports before that already created the root from the process env.
   */
  static applyEnv(): void {
    const root = Logger.getInstance();
    root.logger.level = getLogLevel();
    for (const transport of root.logger.transports) {
      if (transport instanceof winston.transports.Console) {
        transport.format = consoleFormat();
      }
    }
    const requested = process.env.LOG_FORMAT?.trim().toLowerCase();
    if (requested && !(LOG_FORMATS as readonly string[]).includes(requested)) {
      root.warn('Unknown LOG_FORMAT, using text', {
        value: requested.slice(0, 32),
      });
    }
  }

  /** Adds to this logger's own metadata only; its service is fixed by the name it was created with. */
  updateDefaultMeta(metadata: Record<string, any>): void {
    this.defaultMeta = {
      ...this.defaultMeta,
      ...metadata,
    };
  }

  private getCallerInfo(): { filename: string; line: number } {
    const error = new Error();
    Error.captureStackTrace(error);

    // Get the full stack trace
    const stackLines = error.stack?.split('\n') || [];

    // Find the first line that's not from the logger itself
    const callerLine = stackLines.find(
      (line) =>
        !line.includes('logger.service.ts') &&
        !AUDIT_FRAME.test(line) &&
        !line.includes('at Logger.') &&
        line.includes('at '),
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

  private logWithLevel(level: LogLevel, message: string, meta?: any) {
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
    const { fields, rest } = splitFields(meta as unknown);
    this.logger.log({
      level,
      message,
      metadata:
        rest === undefined
          ? undefined
          : this.sanitizeForLogging(
              rest,
              new WeakSet(),
              level === LogLevel.Error,
            ),
      ...this.defaultMeta,
      // Read at emit time; the context part is omitted when there is no context.
      ...recordFields(fields),
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
