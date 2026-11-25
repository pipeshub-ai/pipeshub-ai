import type { NextFunction, Request, Response } from 'express';
import { HarnessLogger, silentHarnessLogger } from '../application/harness-log';
import type { LocalMetrics } from '../application/harness-local-metrics';
import {
  MetricsTarget,
  mergeExpositions,
  metricsTargetsFromEnv,
  PROMETHEUS_TEXT,
  Scrape,
} from '../domain/metrics-exposition';

/** Where the control plane scrapes, and how long it waits for each service. */
export interface MetricsTargets {
  targets: () => readonly MetricsTarget[];
  timeoutMs?: number;
}

export const DEFAULT_METRICS_TARGETS: MetricsTargets = {
  targets: () => metricsTargetsFromEnv(),
};

const DEFAULT_TIMEOUT_MS = 2_000;
// A registry is a few hundred KiB; anything far past it is not one, and is not held in memory whole.
const MAX_BODY_BYTES = 8 * 1024 * 1024;

/**
 * C9.04: `GET …/internal/metrics`, the internal-only services' metrics in one exposition. The caller holds the
 * worker credential (the route's guard); each service is asked with the same credential, concurrently, and one that
 * cannot be read is `scrape_up 0` rather than a failed scrape.
 */
export class AgentHarnessInternalMetricsController {
  constructor(
    private readonly credential: () => Promise<string | null>,
    private readonly config: MetricsTargets = DEFAULT_METRICS_TARGETS,
    private readonly logger: HarnessLogger = silentHarnessLogger,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly local: LocalMetrics | null = null,
  ) {}

  read = async (
    _req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const credential = await this.credential();
      const [scrapes, own] = await Promise.all([
        Promise.all(
          this.config
            .targets()
            .map((target) => this.scrape(target, credential)),
        ),
        this.local?.scrapes() ?? Promise.resolve([]),
      ]);
      res
        .status(200)
        .set('Content-Type', PROMETHEUS_TEXT)
        .set('Cache-Control', 'no-store')
        .end(mergeExpositions([...scrapes, ...own]));
    } catch (error) {
      next(error);
    }
  };

  private async scrape(
    target: MetricsTarget,
    credential: string | null,
  ): Promise<Scrape> {
    if (credential === null) return { service: target.service, body: null };
    try {
      const reply = await this.fetchImpl(target.url, {
        headers: {
          authorization: `Bearer ${credential}`,
          accept: 'text/plain',
        },
        signal: AbortSignal.timeout(
          this.config.timeoutMs ?? DEFAULT_TIMEOUT_MS,
        ),
        redirect: 'error',
      });
      const length = Number(reply.headers.get('content-length') ?? '0');
      if (!reply.ok || length > MAX_BODY_BYTES) {
        await reply.body?.cancel();
        this.logger.warn('agent_harness.metrics.scrape_refused', {
          service: target.service,
          status: reply.status,
        });
        return { service: target.service, body: null };
      }
      const body = await reply.text();
      return {
        service: target.service,
        body: body.length > MAX_BODY_BYTES ? null : body,
      };
    } catch (error) {
      this.logger.warn('agent_harness.metrics.scrape_failed', {
        service: target.service,
        errorType: error instanceof Error ? error.name : typeof error,
      });
      return { service: target.service, body: null };
    }
  }
}
