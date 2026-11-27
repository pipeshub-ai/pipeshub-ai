export const keyValues = {
  API_KEY: 'apiKey',
  PUSH_INTERVAL: 'pushIntervalMs',
  APP_VERSION: 'appVersion',
  ENABLE_METRIC_COLLECTION: 'enableMetricCollection',
  SERVER_URL: 'serverUrl',
  INSTALL_ID: 'installId',
  /** The usage telemetry v3 credential, issued once by the collector. Never logged. */
  INSTALL_SECRET: 'installSecret',
} as const;

export const SERVICE_NAME = 'node_api';
