export interface ErrorMetadata {
  [key: string]: any;
}

export abstract class BaseError extends Error {
  public readonly code: string;
  public readonly statusCode: number;
  public readonly metadata?: ErrorMetadata;
  public readonly timestamp: Date;

  constructor(
    code: string,
    message: string,
    statusCode: number,
    metadata?: ErrorMetadata,
  ) {
    super(message);
    this.name = this.constructor.name;
    this.code = code;
    this.statusCode = statusCode;
    this.metadata = metadata;
    this.timestamp = new Date();

    Error.captureStackTrace(this, this.constructor);
  }

  public toJSON(includeStack: boolean = false): Record<string, any> {
    const json: Record<string, any> = {
      name: this.name,
      code: this.code,
      statusCode: this.statusCode,
      message: this.message,
      metadata: this.metadata,
      timestamp: this.timestamp,
    };
    
    // Only include stack trace if explicitly requested (for server-side logging only)
    // Never expose stack traces to clients - security best practice
    if (includeStack) {
      json.stack = this.stack;
    }
    
    return json;
  }
}

/**
 * Attach the error that caused `error`, the way a native `{ cause }` does
 * (not enumerable, so a response body or a spread never carries it). The log
 * line then says why without the catcher logging it itself. An existing cause
 * is kept. Returns `error`.
 */
export function withCause<E extends Error>(error: E, cause: unknown): E {
  if ((error as { cause?: unknown }).cause === undefined) {
    Object.defineProperty(error, 'cause', {
      value: cause,
      writable: true,
      configurable: true,
      enumerable: false,
    });
  }
  return error;
}
