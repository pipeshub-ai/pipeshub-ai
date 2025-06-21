import winston from 'winston';
import { Request } from 'express';
import { injectable } from 'inversify';
import path from 'path';

export interface LoggerConfig {
  service: string;
  level?: string;
  additionalMeta?: Record<string, any>;
}

@injectable()
export class Logger {
  private static instance: Logger;
  private logger: winston.Logger = {} as winston.Logger;
  private defaultMeta: Record<string, any> = {};

  constructor(config?: LoggerConfig) {
    if (!Logger.instance) {
      this.defaultMeta = {
        service: config?.service || 'default-service',
        ...config?.additionalMeta,
      };

      this.logger = this.initializeLogger(config);
      Logger.instance = this;
    } else if (config) {
      this.updateDefaultMeta({
        service: config.service,
        ...config.additionalMeta,
      });
      if (config.level) {
        this.logger.level = config.level;
      }
    }
    return Logger.instance;
  }

  private initializeLogger(config?: LoggerConfig): winston.Logger {
    const logFormat = winston.format.combine(
      winston.format.timestamp(),
      winston.format.errors({ stack: true }),
      winston.format.printf(({ timestamp, level, message, metadata, stack, filename, line }) => {
        let metaString = '';
        if (metadata) {
          try {
            // Handle circular references by using a custom replacer
            const safeMetadata = this.sanitizeForLogging(metadata);
            metaString = `\nmetadata: ${JSON.stringify(safeMetadata, null, 2)}`;
          } catch (error) {
            metaString = '\nmetadata: [Circular Structure]';
          }
        }
        const stackString = stack ? `\nstack: ${stack}` : '';
        const fileInfo = filename ? `[${filename}:${line}]` : '';

        return `${timestamp} ${fileInfo} ${level}: ${message}${metaString}${stackString}`;
      })
    );

    return winston.createLogger({
      level: config?.level || process.env.LOG_LEVEL || 'info',
      format: logFormat,
      defaultMeta: this.defaultMeta,
      transports: [
        new winston.transports.File({
          filename: 'error.log',
          level: 'error',
          format: winston.format.combine(logFormat, winston.format.json())
        }),
        new winston.transports.File({
          filename: 'combined.log',
          format: winston.format.combine(logFormat, winston.format.json())
        }),
        ...(process.env.NODE_ENV !== 'production'
          ? [new winston.transports.Console({
            format: winston.format.combine(
              winston.format.colorize(),
              logFormat
            )
          })]
          : []),
      ],
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

    // Handle objects
    const sanitized: any = {};
    for (const [key, value] of Object.entries(obj)) {
      // Skip certain properties that might cause circular references
      if (key === 'parent' || key === '_readableState' || key === 'pipes') {
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

  static getInstance(config?: LoggerConfig): Logger {
    if (!Logger.instance) {
      Logger.instance = new Logger(config || { service: 'default-service', level: 'info' });;
    } else if (config) {
      Logger.instance.updateDefaultMeta({
        service: config.service,
        ...config.additionalMeta,
      });
      if (config.level) {
        Logger.instance.logger.level = config.level;
      }
    }
    return Logger.instance;
  }
  

  updateDefaultMeta(metadata: Record<string, any>): void {
    this.defaultMeta = {
      ...this.defaultMeta,
      ...metadata,
    };
    this.logger.defaultMeta = this.defaultMeta;
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
    const callerInfo = this.getCallerInfo();
    this.logger.log({
      level,
      message,
      metadata: meta,
      ...callerInfo,
    });
  }

  error(message: string, meta?: any) {
    this.logWithLevel('error', message, meta);
  }

  warn(message: string, meta?: any) {
    this.logWithLevel('warn', message, meta);
  }

  info(message: string, meta?: any) {
    this.logWithLevel('info', message, meta);
  }

  debug(message: string, meta?: any) {
    this.logWithLevel('debug', message, meta);
  }

  logRequest(req: Request, meta?: any) {
    const callerInfo = this.getCallerInfo();
    this.info(`${req.method} ${req.path}`, {
      ...meta,
      ...callerInfo,
      query: req.query,
      params: req.params,
      headers: this.sanitizeHeaders(req.headers),
    });
  }

  private sanitizeHeaders(headers: any): any {
    const sanitized = { ...headers };
    delete sanitized.authorization;
    delete sanitized.cookie;
    return sanitized;
  }
}