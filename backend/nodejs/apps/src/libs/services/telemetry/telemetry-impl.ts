/** Transitional: re-exports for call sites not yet moved to platform events. */
import type { IncomingHttpHeaders } from 'http';
import { requestClient } from '../../utils/request-client.utils';
import { clientTags as tagsOf } from '../../../plugins/telemetry/identity';

export { recordEvent } from '../../../plugins/telemetry/event-buffer';
export {
  domainFromEmail,
  normalizeOrgId,
} from '../../../plugins/telemetry/identity';
export { signupSource } from '../../utils/request-client.utils';
export {
  isTelemetryEnabled,
  telemetryOffMessage,
  TELEMETRY_COLLECTION_FLAG,
} from '../../../plugins/telemetry/flag';
export { metricsBackend } from '../../../plugins/telemetry/metrics-backend';
export { recordServiceActivity } from '../../../plugins/telemetry/modules/activity-metrics';
export { setMetricCollectionEnabled } from '../../../plugins/telemetry/modules/collection-metrics';
export {
  recordAuthAttempt,
  refusalOutcome,
  signInMethod,
  type AuthAttemptMethod,
} from '../../../plugins/telemetry/modules/auth-metrics';

export const clientTags = (
  headers: IncomingHttpHeaders,
): { user_agent?: string; channel?: string } => tagsOf(requestClient(headers));
