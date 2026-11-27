import { getCurrentOrgId } from '../context/orgContext';
import { Logger } from '../services/logger.service';

const OBSERVATION_WINDOW_MS = 60 * 60 * 1000;
const observedThisWindow = new Set<string>();
let windowStartedAt = 0;

/**
 * The switch for restrictions that could refuse a token caller that works today.
 * Off unless the deployment opts in; only the literal value `enforce` turns it on.
 */
export const tokenRestrictionsEnforced = (): boolean =>
  process.env.TOKEN_RESTRICTIONS_MODE === 'enforce';

type ObservedRequest = {
  method?: string;
  baseUrl?: string;
  route?: { path?: string | RegExp };
};

/** Records that a restriction would have applied, once per org, route and reason per window. */
export function observeTokenRestriction(
  req: ObservedRequest,
  reason: string,
  orgId: string | undefined = getCurrentOrgId(),
): void {
  // The route pattern, not the URL, so ids never reach the log or the dedupe key.
  const route =
    `${req.method ?? ''} ${req.baseUrl ?? ''}${req.route?.path ?? ''}`.trim();
  const now = Date.now();
  if (now - windowStartedAt >= OBSERVATION_WINDOW_MS) {
    observedThisWindow.clear();
    windowStartedAt = now;
  }
  const key = `${orgId}|${route}|${reason}`;
  if (observedThisWindow.has(key)) return;
  observedThisWindow.add(key);
  Logger.getInstance().warn('Token restriction observed', {
    securityEvent: 'token_restriction_observed',
    orgId,
    route,
    reason,
  });
}
