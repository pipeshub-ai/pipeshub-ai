/**
 * The only door into telemetry for code outside this folder. Import from here,
 * never from the files behind it, so a build can swap the whole implementation
 * for `telemetry-impl.stub.ts` (see the build steps that rename the `.stub.ts`
 * files over their twins). The two files must export the same names.
 *
 * Light exports only: heavy ones live in `telemetry-runtime.ts`, because this
 * file is imported while the DB layer loads and must not pull in the app config.
 */
export { recordEvent } from './event-buffer';
export {
  clientTags,
  domainFromEmail,
  normalizeOrgId,
  signupSource,
} from './identity';
export { isTelemetryEnabled, TELEMETRY_COLLECTION_FLAG } from './flag';
export { metricsBackend } from './metrics-backend';
export { recordServiceActivity } from './modules/activity-metrics';
export { setMetricCollectionEnabled } from './modules/collection-metrics';
