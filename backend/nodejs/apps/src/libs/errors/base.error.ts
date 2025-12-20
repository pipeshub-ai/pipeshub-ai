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

  public toJSON(includeStack: boolean = false): Object {
    const json: any = {
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
