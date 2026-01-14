import { BaseError } from './base.error';

/**
 * Base class for configuration-related errors.
 */
export class ConfigurationError extends BaseError {
  public configKey?: string;

  constructor(code: string, message: string, statusCode: number = 500, configKey?: string) {
    super(code, message, statusCode, { configKey });
    this.configKey = configKey;
  }

  override toJSON(includeStack: boolean = false): Record<string, any> {
    return {
      ...super.toJSON(includeStack),
      configKey: this.configKey,
    };
  }
}

/**
 * Raised when required configuration is not found in the KV store.
 */
export class ConfigurationNotFoundError extends ConfigurationError {
  public suggestion: string;

  constructor(configKey: string, suggestion?: string) {
    const defaultSuggestion = 'Please reconfigure this setting in the admin panel.';
    const msg =
      `Configuration not found for key '${configKey}'. ` +
      `The configuration may have been lost during migration or was never set. ` +
      `${suggestion || defaultSuggestion}`;
    super('CONFIGURATION_NOT_FOUND', msg, 404, configKey);
    this.suggestion = suggestion || defaultSuggestion;
  }

  override toJSON(includeStack: boolean = false): Record<string, any> {
    return {
      ...super.toJSON(includeStack),
      suggestion: this.suggestion,
      actionRequired: true,
    };
  }
}

/**
 * Raised when migration from etcd to Redis fails or is incomplete.
 */
export class ConfigurationMigrationError extends ConfigurationError {
  public failedKeys: string[];

  constructor(message: string, failedKeys: string[] = []) {
    const fullMessage =
      `Configuration migration error: ${message}. ` +
      'Please ensure etcd is running and perform migration, or reconfigure the application.';
    super('CONFIGURATION_MIGRATION_ERROR', fullMessage, 500);
    this.failedKeys = failedKeys;
  }

  override toJSON(includeStack: boolean = false): Record<string, any> {
    return {
      ...super.toJSON(includeStack),
      failedKeys: this.failedKeys,
      actionRequired: true,
    };
  }
}

/**
 * Raised when configuration value is invalid or corrupted.
 */
export class ConfigurationInvalidError extends ConfigurationError {
  public reason: string;

  constructor(configKey: string, reason?: string) {
    const defaultReason = 'The configuration value is invalid or corrupted.';
    const msg =
      `Invalid configuration for key '${configKey}'. ${reason || defaultReason} ` +
      'Please reconfigure this setting.';
    super('CONFIGURATION_INVALID', msg, 400, configKey);
    this.reason = reason || defaultReason;
  }

  override toJSON(includeStack: boolean = false): Record<string, any> {
    return {
      ...super.toJSON(includeStack),
      reason: this.reason,
      actionRequired: true,
    };
  }
}
