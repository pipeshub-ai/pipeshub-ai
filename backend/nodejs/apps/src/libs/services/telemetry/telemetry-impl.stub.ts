/** Same exports as `telemetry-impl.ts`; every one does nothing. */
export {
  clientTags,
  domainFromEmail,
  normalizeOrgId,
  signupSource,
} from './identity';

type Labels = Record<string, string | number>;

export const TELEMETRY_COLLECTION_FLAG = 'TELEMETRY_COLLECTION_FLAG';

export const isTelemetryEnabled = (): boolean => false;

export const recordEvent = (
  _event: string,
  _props?: Record<string, unknown>,
): void => {};

export const recordServiceActivity = (
  _activityName: string,
  _labels: { status?: string; org?: string; domain?: string } = {},
): void => {};

export const setMetricCollectionEnabled = (
  _org: string,
  _enabled: boolean,
): void => {};

const instrument = {
  inc(_labels?: Labels, _value?: number): void {},
  set(_labels: Labels, _value: number): void {},
  observe(_labels: Labels, _value: number): void {},
  reset(): void {},
};

export const metricsBackend = {
  createCounter: (_def: unknown): typeof instrument => instrument,
  createGauge: (_def: unknown): typeof instrument => instrument,
  createHistogram: (_def: unknown): typeof instrument => instrument,
};
