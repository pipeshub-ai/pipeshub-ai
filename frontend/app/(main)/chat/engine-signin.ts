import type { SSEEngineProgressEvent } from './types';

/** A person's own sign-in to the engine (SIGNIN), as the run's `signin` progress lines tell it. */
export interface EngineSignInLine {
  attemptId: string;
  state: 'requested' | 'signed_in' | 'failed';
  reasonCode: string | null;
  plan: string | null;
}

const STATES: readonly EngineSignInLine['state'][] = ['requested', 'signed_in', 'failed'];

/** The latest sign-in line of the run's frames, or null when it never asked. */
export function latestSignIn(frames: readonly SSEEngineProgressEvent[]): EngineSignInLine | null {
  let latest: EngineSignInLine | null = null;
  for (const frame of frames) {
    if (frame.kind !== 'signin' || !frame.data) continue;
    const { attemptId, state, reasonCode, plan } = frame.data as Record<string, unknown>;
    const known = STATES.find((s) => s === state);
    if (typeof attemptId !== 'string' || known === undefined) continue;
    latest = {
      attemptId,
      state: known,
      reasonCode: typeof reasonCode === 'string' ? reasonCode : null,
      plan: typeof plan === 'string' ? plan : null,
    };
  }
  return latest;
}

/** Whether the run signed in on the person's plan (its header says so; no dollar cost is shown). */
export const onOwnPlan = (frames: readonly SSEEngineProgressEvent[]): boolean =>
  frames.some((frame) => frame.kind === 'signin' && (frame.data as { state?: unknown } | undefined)?.state === 'signed_in');

/** The copy key of a sign-in refusal or failure code, or null for any other code. Literal keys for `i18n:keys`. */
export function signInReasonKey(code: string | null | undefined): string | null {
  switch (code?.toLowerCase()) {
    case 'signin_code_invalid':
      return 'engineSignIn.reason.codeInvalid';
    case 'signin_failed':
      return 'engineSignIn.reason.failed';
    case 'plan_not_allowed':
      return 'engineSignIn.reason.planNotAllowed';
    case 'signin_timeout':
      return 'engineSignIn.reason.timeout';
    case 'signin_cancelled':
      return 'engineSignIn.reason.cancelled';
    case 'signin_url_refused':
      return 'engineSignIn.reason.urlRefused';
    case 'signin_unavailable':
      return 'engineSignIn.reason.unavailable';
    case 'account_disconnected':
      return 'engineSignIn.reason.disconnected';
    case 'account_expired':
      return 'engineSignIn.reason.expired';
    case 'plan_limit_reached':
      return 'engineSignIn.reason.planLimit';
    case 'signin_required':
      return 'engineSignIn.reason.required';
    case 'account_shared_session':
      return 'engineSignIn.reason.shared';
    case 'account_requires_interactive':
      return 'engineSignIn.reason.interactive';
    case 'engine_signin_off':
      return 'engineSignIn.reason.orgOff';
    default:
      return null;
  }
}
