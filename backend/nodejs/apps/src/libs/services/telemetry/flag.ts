import {
  METRICS_CONSENT_DEFAULT,
  TELEMETRY_DEFAULT_ENABLED,
} from '../../../edition';

/**
 * Master switch for telemetry. `TELEMETRY_COLLECTION_FLAG` decides whether any
 * telemetry code runs (no middleware, no registry, no event buffer, no push
 * loop, no gauge refresh when off). Unset or empty falls back to the edition
 * default in `src/edition.ts`. The per-install `enableMetricCollection`
 * consent in the KV store only matters once this flag is on.
 */
export const TELEMETRY_COLLECTION_FLAG = 'TELEMETRY_COLLECTION_FLAG';

export function isTelemetryEnabled(): boolean {
  const raw = process.env[TELEMETRY_COLLECTION_FLAG]?.trim();
  if (!raw) {
    return TELEMETRY_DEFAULT_ENABLED;
  }
  return raw.toLowerCase() === 'true';
}

let consentGiven = METRICS_CONSENT_DEFAULT;

/** The admin's Data Collection switch, as the telemetry service last read it. */
export function setCollectionConsent(given: boolean): void {
  consentGiven = given;
}

/**
 * Whether anything may be recorded right now: the deployment allows telemetry
 * and an admin has switched Data Collection on, so the admin can always see and
 * stop whatever is collected.
 */
export function isCollecting(): boolean {
  return isTelemetryEnabled() && consentGiven;
}
