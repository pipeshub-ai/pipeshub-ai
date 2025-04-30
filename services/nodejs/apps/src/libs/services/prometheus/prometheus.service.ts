import * as promClient from 'prom-client';
import { Mutex } from 'async-mutex';
import { inject, injectable } from 'inversify';
import axios from 'axios';
import * as https from 'https';
import { Logger } from '../logger.service';
import { KeyValueStoreService } from '../keyValueStore.service';
import { keyValues } from './constants';
import { parseBoolean } from '../../../modules/storage/utils/utils';
import { configPaths } from '../../../modules/configuration_manager/paths/paths';

const logger = Logger.getInstance({ service: 'Prometheus Service' });

const DEFAULTS = {
  METRIC_HOST: 'https://metrics-collector.intellysense.com/collect-metrics',
  PUSH_INTERVAL: 60000,
  APP_VERSION: '1.0.0',
};

const TIMEOUT_MS = 10000;
const START_DELAY_MS = 200;

interface MetricsConfig {
  serverUrl?: string;
  apiKey?: string;
  appVersion?: string;
  pushIntervalMs?: string;
  enableMetricCollection?: string;
  [key: string]: string | undefined;
}

@injectable()
export class PrometheusService {
  private register!: promClient.Registry;
  private static instance: PrometheusService;
  private metricsServerUrl: string = DEFAULTS.METRIC_HOST;
  private apiKey: string = '';
  private instanceId: string = '';
  private appVersion: string = DEFAULTS.APP_VERSION;
  private pushIntervalMs: number = DEFAULTS.PUSH_INTERVAL;
  private enableMetricCollection: boolean = true;
  private pushInterval: NodeJS.Timeout | null = null;
  private isStarting: boolean = false;
  private activityCounter!: promClient.Counter<string>;
  private mutex: Mutex = new Mutex();

  constructor(
    @inject('KeyValueStoreService') private kvStore: KeyValueStoreService,
  ) {
    logger.info('Initializing PrometheusService');
    if (PrometheusService.instance) {
      return PrometheusService.instance;
    }

    this.register = new promClient.Registry();

    // Initialize the activity counter
    this.activityCounter = new promClient.Counter({
      name: 'app_activity_total',
      help: 'Total number of activities recorded',
      labelNames: [
        'activity',
        'userId',
        'orgId',
        'email',
        'requestId',
        'method',
        'path',
        'reqContext',
        'statusCode',
      ],
      registers: [this.register],
    });

    this.init();
    PrometheusService.instance = this;
    logger.info('PrometheusService initialized successfully');
  }

  async init(): Promise<void> {
    await this.initializeMetricsCollection();
    this.watchKeysForMetricsCollection(configPaths.metricsCollection);
  }

  private async initializeMetricsCollection(): Promise<void> {
    try {
      await Promise.all([
        await this.loadServerUrl(),
        await this.loadApiKey(),
        await this.loadInstanceId(),
        await this.loadAppVersion(),
        await this.loadPushInterval(),
        await this.loadMetricCollectionFlag(),
      ]);
      this.logConfig();
      await this.startOrStopMetricCollection();
    } catch (error) {
      logger.error('Failed to initialize metrics collection:', error);
      this.metricsServerUrl = this.getEnv(
        'METRICS_SERVER_URL',
        DEFAULTS.METRIC_HOST,
      );
    }
  }

  private async loadServerUrl(): Promise<void> {
    this.metricsServerUrl = await this.getOrSet(
      keyValues.SERVER_URL,
      this.getEnv('METRICS_SERVER_URL', DEFAULTS.METRIC_HOST),
    );
  }

  private async loadApiKey(): Promise<void> {
    this.apiKey = await this.getOrSet(
      keyValues.API_KEY,
      this.generateInstanceId(),
    );
  }

  private async loadInstanceId(): Promise<void> {
    this.instanceId = this.generateInstanceId();
  }

  private async loadAppVersion(): Promise<void> {
    this.appVersion = await this.getOrSet(
      keyValues.APP_VERSION,
      DEFAULTS.APP_VERSION,
    );
  }

  private async loadPushInterval(): Promise<void> {
    this.pushIntervalMs = parseInt(
      await this.getOrSet(
        keyValues.PUSH_INTERVAL,
        String(DEFAULTS.PUSH_INTERVAL),
      ),
      10,
    );
  }

  private async loadMetricCollectionFlag(): Promise<void> {
    this.enableMetricCollection = parseBoolean(
      await this.getOrSet(keyValues.ENABLE_METRIC_COLLECTION, 'true'),
    );
  }

  private watchKeysForMetricsCollection(key: string): void {
    this.kvStore.watchKey(key, this.initializeMetricsCollection.bind(this));
  }

  private async startOrStopMetricCollection(): Promise<void> {
    const config = await this.getConfig();
    const currentValue = parseBoolean(
      config[keyValues.ENABLE_METRIC_COLLECTION] || 'true',
    );

    if (currentValue) {
      logger.debug('Starting metrics push');
      await this.startMetricsPush();
    } else {
      logger.debug('Stopping metrics push');
      this.stopMetricsPush();
    }
  }

  private async getConfig(): Promise<MetricsConfig> {
    try {
      return JSON.parse(
        (await this.kvStore.get<string>(configPaths.metricsCollection)) || '{}',
      );
    } catch (error) {
      logger.error('Failed to parse metrics config:', error);
      return {};
    }
  }

  private async getOrSet(key: string, defaultValue: string): Promise<string> {
    const config = await this.getConfig();
    if (!(key in config)) {
      config[key] = defaultValue;
      await this.kvStore.set<string>(
        configPaths.metricsCollection,
        JSON.stringify(config),
      );
      return defaultValue;
    }
    return config[key]!;
  }

  private async startMetricsPush(): Promise<void> {
    if (this.isStarting) return;
    const release = await this.mutex.acquire();
    this.isStarting = true;
    try {
      this.stopMetricsPush();
      await new Promise((resolve) => setTimeout(resolve, START_DELAY_MS));
      this.pushInterval = setInterval(() => {
        this.pushMetricsToServer().catch((err) =>
          logger.error('Failed to push metrics:', err),
        );
      }, this.pushIntervalMs);
      logger.debug(`Started pushing metrics every ${this.pushIntervalMs}ms`);
    } finally {
      this.isStarting = false;
      release(); // Release the mutex in the finally block
    }
  }

  private stopMetricsPush(): void {
    if (this.pushInterval) {
      clearInterval(this.pushInterval);
      this.pushInterval = null;
      logger.debug('Metrics push stopped');
    }
  }

  private async pushMetricsToServer(): Promise<void> {
    try {
      const metricsText = await this.register.metrics();
      await axios.post(
        this.metricsServerUrl,
        {
          metrics: metricsText,
          instanceId: this.instanceId,
          version: this.appVersion,
          timestamp: new Date().toISOString(),
        },
        {
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${this.apiKey}`,
          },
          timeout: TIMEOUT_MS,
          httpsAgent: this.createHttpsAgent(),
          maxContentLength: Infinity,
          maxBodyLength: Infinity,
          decompress: true,
        },
      );
      logger.debug('Successfully pushed metrics to server');
    } catch (error: any) {
      this.handlePushError(error);
    }
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

  private handlePushError(error: any): void {
    if (error.response) {
      logger.error('Server responded with error:', {
        status: error.response.status,
        serverUrl: this.metricsServerUrl,
      });
    } else if (error.request) {
      logger.error('No response received:', {
        request: error.request._currentUrl || error.request.path,
        error: error.message,
      });
    } else {
      logger.error('Request setup error:', {
        message: error.message,
        stack: error.stack,
      });
    }
  }

  private generateInstanceId(): string {
    const crypto = require('crypto');
    const hash = crypto
      .createHash('sha256')
      .update(process.env.HOSTNAME || Math.random().toString())
      .digest('hex');
    return hash.substring(0, 16);
  }

  private getEnv(key: string, fallback: string): string {
    return process.env[key] || fallback;
  }

  private logConfig(): void {
    if (process.env.NODE_ENV == 'development') {
      logger.debug('Prometheus Configuration', {
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
    return this.register.metrics();
  }

  public recordActivity(
    activity: string,
    userId?: string,
    orgId?: string,
    email?: string,
    requestId?: string,
    method?: string,
    path?: string,
    reqContext?: any,
    statusCode?: number,
  ): void {
    this.activityCounter.inc({
      activity,
      userId: userId || 'anonymous',
      orgId: orgId || 'anonymous',
      email,
      requestId,
      method,
      path,
      reqContext,
      statusCode: statusCode || undefined,
    });
  }
}
