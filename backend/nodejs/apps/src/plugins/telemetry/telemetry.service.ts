import { Mutex } from 'async-mutex';
import { inject, injectable } from 'inversify';
import axios from 'axios';
import * as https from 'https';
import { Logger } from '../../libs/services/logger.service';
import { KeyValueStoreService } from '../../libs/services/keyValueStore.service';
import { keyValues, SERVICE_NAME } from './constants';
import { eventBuffer } from './event-buffer';
import {
  HEADER_REQUEST_ID,
  newSystemRoot,
} from '../../libs/context/request-context';
import { metricsBackend } from './metrics-backend';
import { setInstallInfo } from './modules/install-metrics';
import { createHash, randomUUID } from 'crypto';
const SCHEMA_VERSION = 1;
const METRICS_VERSION = '2';
import { parseBoolean } from '../../modules/storage/utils/utils';
import { configPaths } from '../../modules/configuration_manager/paths/paths';
import { EncryptionService } from '../../libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../../modules/configuration_manager/config/config';
import { axiosV3Http } from './v3/http';
import { UsageV3Sender, usageContractMode } from './v3/sender';

const logger = Logger.getInstance({ service: 'Telemetry Service' });

const DEFAULTS = {
  METRIC_HOST: 'https://metrics-collector.intellysense.com/collect-metrics',
  PUSH_INTERVAL: 300000,
  APP_VERSION: '1.0.0',
};

const TIMEOUT_MS = 10000;
const START_DELAY_MS = 200;

const firstNonEmpty = (...values: (string | undefined)[]): string => {
  for (const value of values) {
    if (value != null && value !== '') {
      return value;
    }
  }
  return '';
};

interface MetricsConfig {
  serverUrl?: string;
  apiKey?: string;
  appVersion?: string;
  pushIntervalMs?: string;
  enableMetricCollection?: string;
  [key: string]: string | undefined;
}

// Owns telemetry config, the push loop, and current-state refreshes. Metric
// definitions live in module files that register against the shared metricsBackend.
export interface ITelemetryService {
  getMetrics(): Promise<string>;
}

@injectable()
export class TelemetryService implements ITelemetryService {
  private static instance: TelemetryService | undefined;
  private metricsServerUrl: string = DEFAULTS.METRIC_HOST;
  private apiKey: string = '';
  private instanceId: string = '';
  private appVersion: string = DEFAULTS.APP_VERSION;
  private pushIntervalMs: number = DEFAULTS.PUSH_INTERVAL;
  private enableMetricCollection: boolean = true;
  private pushInterval: NodeJS.Timeout | null = null;
  private isStarting: boolean = false;
  private mutex: Mutex = new Mutex();
  private pushing: Promise<void> | null = null;
  // Usage telemetry v3: registers the install once, then sends over v3 and
  // says each interval whether v3 or the legacy endpoints carry the data.
  private readonly v3: UsageV3Sender = new UsageV3Sender({
    mode: usageContractMode(),
    http: axiosV3Http({
      timeoutMs: TIMEOUT_MS,
      httpsAgent: () => this.createHttpsAgent(),
      headers: () => ({ [HEADER_REQUEST_ID]: newSystemRoot() }),
    }),
    secrets: {
      read: () => this.readInstallSecret(),
      write: (secret) => this.writeInstallSecret(secret),
    },
    logger,
    snapshot: () => metricsBackend.snapshot(),
    events: eventBuffer,
  });

  constructor(
    @inject('KeyValueStoreService') private kvStore: KeyValueStoreService,
  ) {
    logger.info('Initializing TelemetryService');
    if (TelemetryService.instance != null) {
      return TelemetryService.instance;
    }

    this.init().catch((err: unknown) => {
      logger.warn('TelemetryService init failed:', err);
    });
    TelemetryService.instance = this;
    logger.info('TelemetryService initialized successfully');
  }

  async init(): Promise<void> {
    await this.initializeMetricsCollection();
    this.watchKeysForMetricsCollection(configPaths.metricsCollection);
  }

  private async initializeMetricsCollection(): Promise<void> {
    try {
      const config = await this.getConfig();
      await this.persistMissingDefaults(config);

      this.metricsServerUrl =
        config[keyValues.SERVER_URL] ??
        this.getEnv('METRICS_SERVER_URL', DEFAULTS.METRIC_HOST);
      this.apiKey = config[keyValues.API_KEY] ?? '';
      this.instanceId = config[keyValues.INSTALL_ID] ?? '';
      this.appVersion = config[keyValues.APP_VERSION] ?? DEFAULTS.APP_VERSION;
      const parsedInterval = parseInt(
        firstNonEmpty(
          config[keyValues.PUSH_INTERVAL],
          String(DEFAULTS.PUSH_INTERVAL),
        ),
        10,
      );
      // NaN or <=0 would make setInterval fire ~every ms.
      this.pushIntervalMs =
        Number.isFinite(parsedInterval) && parsedInterval > 0
          ? parsedInterval
          : DEFAULTS.PUSH_INTERVAL;
      this.enableMetricCollection = parseBoolean(
        firstNonEmpty(config[keyValues.ENABLE_METRIC_COLLECTION], 'true'),
      );

      this.logConfig();
      await this.startOrStopMetricCollection();
    } catch (error) {
      logger.warn('Failed to initialize metrics collection:', {
        error:
          error instanceof Error
            ? { message: error.message, stack: error.stack }
            : error,
      });
      this.metricsServerUrl = this.getEnv(
        'METRICS_SERVER_URL',
        DEFAULTS.METRIC_HOST,
      );
    }
  }

  private async persistMissingDefaults(config: MetricsConfig): Promise<void> {
    const defaults: [string, () => string][] = [
      [
        keyValues.SERVER_URL,
        () => this.getEnv('METRICS_SERVER_URL', DEFAULTS.METRIC_HOST),
      ],
      [keyValues.API_KEY, () => this.generateInstanceId()],
      [keyValues.INSTALL_ID, () => this.generateInstallId()],
      [keyValues.APP_VERSION, () => DEFAULTS.APP_VERSION],
      [keyValues.PUSH_INTERVAL, () => String(DEFAULTS.PUSH_INTERVAL)],
      [keyValues.ENABLE_METRIC_COLLECTION, () => 'true'],
    ];
    let changed = false;
    for (const [key, makeDefault] of defaults) {
      const stored = config[key];
      if (stored == null || stored.trim() === '') {
        config[key] = makeDefault();
        changed = true;
      }
    }
    if (changed) {
      await this.kvStore.set<string>(
        configPaths.metricsCollection,
        this.getEncryptionService().encrypt(JSON.stringify(config)),
      );
    }
  }

  private watchKeysForMetricsCollection(key: string): void {
    void this.kvStore.watchKey(key, () => {
      this.initializeMetricsCollection().catch((err: unknown) => {
        logger.warn('Failed to reload metrics collection config:', err);
      });
    });
  }

  private async startOrStopMetricCollection(): Promise<void> {
    if (this.enableMetricCollection) {
      logger.debug('Starting metrics push');
      await this.startMetricsPush();
    } else {
      logger.debug('Stopping metrics push');
      this.stopMetricsPush();
    }
  }

  private getEncryptionService(): EncryptionService {
    const cfg = loadConfigurationManagerConfig();
    return EncryptionService.getInstance(cfg.algorithm, cfg.secretKey);
  }

  private async getConfig(): Promise<MetricsConfig> {
    const raw = await this.kvStore.get<string>(configPaths.metricsCollection);
    if (raw == null || raw === '') {
      return {};
    }

    let serialized: string;
    try {
      serialized = this.getEncryptionService().decrypt(raw);
    } catch {
      serialized = raw;
    }

    try {
      const parsed: unknown = JSON.parse(serialized);
      return typeof parsed === 'object' && parsed !== null
        ? (parsed as MetricsConfig)
        : {};
    } catch (error) {
      logger.warn('Failed to parse metrics config:', {
        error: error instanceof Error ? error.message : error,
      });
      return {};
    }
  }

  private async startMetricsPush(): Promise<void> {
    if (this.isStarting) return;
    const release = await this.mutex.acquire();
    this.isStarting = true;
    try {
      this.stopMetricsPush();
      await new Promise((resolve) => setTimeout(resolve, START_DELAY_MS));
      this.pushInterval = setInterval(() => {
        if (!this.isServerUrlValid()) {
          logger.debug(
            'Skipping telemetry push - metrics server URL not configured',
          );
          return;
        }
        this.pushOnce().catch((err: unknown) => {
          logger.warn('Failed to push telemetry:', err);
        });
        this.refreshInstallInfo().catch((err: unknown) => {
          logger.warn('Failed to refresh install info:', err);
        });
      }, this.pushIntervalMs);
      logger.debug(
        `Started pushing metrics every ${String(this.pushIntervalMs)}ms`,
      );
    } finally {
      this.isStarting = false;
      release();
    }
  }

  private stopMetricsPush(): void {
    if (this.pushInterval) {
      clearInterval(this.pushInterval);
      this.pushInterval = null;
      logger.debug('Metrics push stopped');
    }
  }

  private hasActualMetrics(metricsText: string): boolean {
    const lines = metricsText
      .split('\n')
      .filter((line) => line.trim().length > 0);
    return lines.some((line) => !line.trim().startsWith('#'));
  }

  /**
   * One interval's push. The v3 sender goes first: it sends over v3 when the
   * collector speaks it, and otherwise says the legacy endpoints carry this
   * interval (or that nothing is to be sent now and everything is kept).
   * Pushes never overlap: a second call waits for the one in flight.
   */
  private pushOnce(): Promise<void> {
    const run = (this.pushing ?? Promise.resolve())
      .then(() => this.pushOnceNow())
      .finally(() => {
        if (this.pushing === run) this.pushing = null;
      });
    this.pushing = run;
    return run;
  }

  private async pushOnceNow(): Promise<void> {
    if (this.instanceId === '') {
      logger.debug('Skipping telemetry push - installId not initialized');
      return;
    }
    let route: 'v3' | 'legacy' | 'none';
    try {
      route = await this.v3.tick({
        serverUrl: this.metricsServerUrl,
        installId: this.instanceId,
        version: this.appVersion,
        service: SERVICE_NAME,
      });
    } catch (error) {
      // Nothing is sent the other way either: the registry and the buffer keep it.
      logger.warn('Usage telemetry v3 push failed:', {
        message: error instanceof Error ? error.message : 'unknown error',
      });
      return;
    }
    if (route !== 'legacy') {
      return;
    }
    const delivered = await this.pushMetricsToServer();
    await this.shipEventsToServer();
    if (delivered) {
      await this.v3.deliveredByLegacy();
    }
  }

  private async readInstallSecret(): Promise<string | undefined> {
    const secret = (await this.getConfig())[keyValues.INSTALL_SECRET];
    return secret != null && secret !== '' ? secret : undefined;
  }

  /**
   * Stores the secret next to the install id, in the same encrypted value.
   * Refuses when the stored configuration is not this install's, so a failed
   * read cannot replace the configuration with a value that holds only a secret.
   */
  private async writeInstallSecret(secret: string): Promise<void> {
    const config = await this.getConfig();
    if (config[keyValues.INSTALL_ID] !== this.instanceId) {
      throw new Error('stored telemetry configuration is not readable');
    }
    config[keyValues.INSTALL_SECRET] = secret;
    await this.kvStore.set<string>(
      configPaths.metricsCollection,
      this.getEncryptionService().encrypt(JSON.stringify(config)),
    );
  }

  /** True when the collector accepted the push. */
  private async pushMetricsToServer(): Promise<boolean> {
    if (this.instanceId === '') {
      logger.debug('Skipping metrics push - installId not initialized');
      return false;
    }
    try {
      const metricsText = await metricsBackend.serialize();

      if (!this.hasActualMetrics(metricsText)) {
        logger.debug('Skipping metrics push - no actual metrics data');
        return false;
      }

      await axios.post(
        this.metricsServerUrl,
        {
          metrics: metricsText,
          instanceId: this.instanceId,
          version: this.appVersion,
          metricsVersion: METRICS_VERSION,
          timestamp: new Date().toISOString(),
        },
        {
          headers: this.buildHeaders(),
          timeout: TIMEOUT_MS,
          httpsAgent: this.createHttpsAgent(),
          maxContentLength: Infinity,
          maxBodyLength: Infinity,
          decompress: true,
        },
      );
      logger.debug('Successfully pushed metrics to server');
      return true;
    } catch (error: unknown) {
      // The registry only grows, so a 413 would otherwise recur until restart.
      if (axios.isAxiosError(error) && error.response?.status === 413) {
        logger.warn(
          'Collector rejected metrics payload as too large; resetting metric registry',
          { serverUrl: this.metricsServerUrl },
        );
        metricsBackend.reset();
        return false;
      }
      this.handlePushError(error);
      return false;
    }
  }

  private deriveEventsUrl(): string {
    try {
      const url = new URL(this.metricsServerUrl);
      url.pathname = url.pathname.replace(
        /collect-metrics\/?$/,
        'collect-events',
      );
      if (!url.pathname.includes('collect-events')) {
        url.pathname = '/collect-events';
      }
      return url.toString();
    } catch {
      return this.metricsServerUrl.replace('collect-metrics', 'collect-events');
    }
  }

  private async shipEventsToServer(): Promise<void> {
    if (this.instanceId === '') {
      logger.debug('Skipping event shipment - installId not initialized');
      return;
    }
    const events = eventBuffer.drain();
    if (events.length === 0) {
      return;
    }
    try {
      await axios.post(
        this.deriveEventsUrl(),
        {
          instanceId: this.instanceId,
          service: SERVICE_NAME,
          version: this.appVersion,
          metricsVersion: METRICS_VERSION,
          schemaVersion: SCHEMA_VERSION,
          events,
        },
        {
          headers: this.buildHeaders(),
          timeout: TIMEOUT_MS,
          httpsAgent: this.createHttpsAgent(),
        },
      );
      logger.debug(`Shipped ${String(events.length)} event(s) to collector`);
    } catch (error: unknown) {
      this.handlePushError(error);
    }
  }

  private isServerUrlValid(): boolean {
    if (this.metricsServerUrl === '') {
      return false;
    }
    try {
      new URL(this.metricsServerUrl);
      return true;
    } catch {
      return false;
    }
  }

  private buildHeaders(): Record<string, string> {
    return {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${this.apiKey}`,
      'X-Metrics-Version': METRICS_VERSION,
      [HEADER_REQUEST_ID]: newSystemRoot(),
    };
  }

  private createHttpsAgent(): https.Agent {
    return new https.Agent({
      rejectUnauthorized: true,
      minVersion: 'TLSv1.2',
      maxVersion: 'TLSv1.3',
      ciphers:
        'ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384',
      honorCipherOrder: true,
    });
  }

  private handlePushError(error: unknown): void {
    if (axios.isAxiosError(error)) {
      if (error.response) {
        logger.warn('Server responded with error:', {
          status: error.response.status,
          serverUrl: this.metricsServerUrl,
        });
      } else {
        logger.warn('No response received:', {
          url: error.config?.url ?? this.metricsServerUrl,
          error: error.message,
        });
      }
      return;
    }
    logger.warn('Request setup error:', {
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    });
  }

  private generateInstanceId(): string {
    return createHash('sha256')
      .update(firstNonEmpty(process.env.HOSTNAME, Math.random().toString()))
      .digest('hex')
      .substring(0, 16);
  }

  private generateInstallId(): string {
    return randomUUID();
  }

  private getEnv(key: string, fallback: string): string {
    return firstNonEmpty(process.env[key], fallback);
  }

  private logConfig(): void {
    if (process.env.NODE_ENV == 'development') {
      logger.debug('Telemetry Configuration', {
        metricsServerUrl: this.metricsServerUrl,
        apiKey: this.apiKey,
        instanceId: this.instanceId,
        appVersion: this.appVersion,
        pushIntervalMs: this.pushIntervalMs,
        enableMetricCollection: this.enableMetricCollection,
      });
    }
  }

  public async getMetrics(): Promise<string> {
    return metricsBackend.serialize();
  }

  /** The running singleton, or undefined if telemetry hasn't started yet. */
  public static current(): TelemetryService | undefined {
    return TelemetryService.instance;
  }

  /**
   * Push metrics and events to the collector immediately, outside the interval.
   * Used to ship a consent opt-out before the flag flips off and stops the loop.
   */
  public async flush(): Promise<void> {
    if (!this.isServerUrlValid()) {
      return;
    }
    await this.pushOnce();
  }

  /**
   * Stops the push loop and, when the install still consents, ships what is
   * buffered so a clean stop does not lose the last interval.
   */
  public async shutdown(): Promise<void> {
    this.stopMetricsPush();
    if (this.enableMetricCollection) {
      await this.flush();
    }
    if (TelemetryService.instance === this) {
      TelemetryService.instance = undefined;
    }
  }

  private async refreshInstallInfo(): Promise<void> {
    try {
      let deployment: Record<string, string> = {};
      const raw = await this.kvStore.get<string>(configPaths.deployment);
      if (raw != null && raw !== '') {
        let parsed: unknown = JSON.parse(raw);
        if (typeof parsed === 'string') {
          parsed = JSON.parse(parsed);
        }
        if (typeof parsed === 'object' && parsed !== null) {
          deployment = parsed as Record<string, string>;
        }
      }
      setInstallInfo({
        graph_db: firstNonEmpty(
          deployment.dataStoreType,
          process.env.DATA_STORE,
          'unknown',
        ),
        vector_db: firstNonEmpty(deployment.vectorDbType, 'qdrant'),
        message_broker: firstNonEmpty(
          deployment.messageBrokerType,
          process.env.MESSAGE_BROKER,
          'redis',
        ),
        kv_store: firstNonEmpty(
          deployment.kvStoreType,
          process.env.KV_STORE_TYPE,
          'redis',
        ),
      });
    } catch (error) {
      logger.warn('Failed to refresh install info:', error);
    }
  }
}

export function startTelemetry(kvStore: KeyValueStoreService): void {
  new TelemetryService(kvStore);
}
