import type { NextFunction, Request, Response } from 'express';

/** Same exports as `telemetry-runtime.ts`; every one does nothing. */
export const TelemetryService = {
  current: (): { flush(): Promise<void> } | undefined => undefined,
};

export const startTelemetry = (_kvStore: unknown): void => {};

export const startOrgMetricsRefresh = (_logger: unknown): void => {};

export const metricsMiddleware =
  () =>
  (_req: Request, _res: Response, next: NextFunction): void =>
    next();
