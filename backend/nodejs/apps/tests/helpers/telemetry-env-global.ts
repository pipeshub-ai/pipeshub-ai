/**
 * Loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * With telemetry off (TELEMETRY_COLLECTION_FLAG false, or an edition default of
 * off) the metrics backend is a no-op chosen at import time. The suite exercises
 * the real prom-client backend, so the flag is set before any src module loads.
 * Tests for the disabled path stub `isTelemetryEnabled` or set the env per test.
 * Collection also needs the admin's consent, which the suite switches on the same way.
 */
import { setCollectionConsent } from '../../src/libs/services/telemetry/flag';

if (process.env.TELEMETRY_COLLECTION_FLAG == null) {
  process.env.TELEMETRY_COLLECTION_FLAG = 'true';
}
setCollectionConsent(true);
