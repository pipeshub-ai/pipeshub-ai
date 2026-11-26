/**
 * A person's own Claude account in Claude Code (SIGNIN, C-1): the rules every
 * surface shares. The person signs in to the unmodified binary inside their
 * own sandbox; PipesHub keeps only what Claude Code reports afterwards (email
 * and plan kind), never a token or a code. Used only for the owner's own
 * interactive web turns, never as a silent fallback.
 */

export const CLAUDE_PLAN_ENGINE = 'claude_code';
export const CLAUDE_PLAN_PROVIDER = 'anthropic';
/** The vendor row this release ships for it (A1 VR-10). */
export const CLAUDE_PLAN_ROW = 'anthropic-claude-plan-claude-code';

/** The pinned account of a run on the person's own plan; the worker checks it names the run's actor. */
export const planAccountId = (userId: string): string =>
  `users/${userId}/claude-plan`;

/** Plans an organization administers (Claude Code's `subscriptionType`); any other is a consumer plan. */
export const WORK_PLANS: ReadonlySet<string> = new Set(['team', 'enterprise']);

/** The line Anthropic's page shows and Claude Code's prompt takes: `<code>#<state>` (SIGNIN-1, SP-2). */
export const SIGNIN_CODE = /^[A-Za-z0-9._~-]{1,512}#[A-Za-z0-9._~-]{1,512}$/;
/** A sign-in attempt id as the worker names it (`sin_<attempt>`). */
export const SIGNIN_ATTEMPT = /^sin_[A-Za-z0-9._:-]{1,200}$/;

/** Where the turn that would use the plan came from. */
export interface SignInContext {
  /** How the actor reached the run (`x.pipeshub.via`): only `session` is a person at the web app. */
  via: string;
  /** Where it was started, when the run says (`web`, `slack`, `mcp`). */
  surface?: string;
  /** The conversation has anyone besides its owner. */
  shared: boolean;
}

export type SignInRefusalCode =
  'account_requires_interactive' | 'account_shared_session';

/**
 * Why the person's plan may not be used for this turn, or null. Scheduled and
 * triggered runs, service accounts, connected apps, tokens, Slack and MCP are
 * not the person at the web app; a shared conversation is not theirs alone.
 */
export function signInRefusal(
  context: SignInContext,
): SignInRefusalCode | null {
  if (
    context.via !== 'session' ||
    (context.surface !== undefined && context.surface !== 'web')
  ) {
    return 'account_requires_interactive';
  }
  return context.shared ? 'account_shared_session' : null;
}

/** What is stored per person: the switch and Claude Code's last report. Never a token or a code. */
export interface StoredEngineSignIn {
  enabled: boolean;
  email?: string;
  plan?: string;
  signedInAt?: number;
  disconnectedAt?: number;
}

/**
 * SIGNIN-5: how long a signed-in sandbox is kept between a conversation's turns, as the harness worker reads it
 * (`signin_keep` in `app/harness/engines/worker.py`, same variables and bounds). The worker refuses to start on a
 * value out of bounds, so here such a value is shown as the default the worker never runs with: it does not run.
 */
export interface SignInKeep {
  idleMinutes: number;
  maxHours: number;
}

export const SIGNIN_KEEP_DEFAULT: SignInKeep = {
  idleMinutes: 30,
  maxHours: 12,
};

const bounded = (
  raw: string | undefined,
  min: number,
  max: number,
  fallback: number,
): number => {
  const text = (raw ?? '').trim();
  if (!/^\d+$/.test(text)) return fallback;
  const value = Number(text);
  return value >= min && value <= max ? value : fallback;
};

export function signInKeepFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): SignInKeep {
  return {
    idleMinutes: bounded(
      env['HARNESS_SIGNIN_IDLE_MINUTES'],
      5,
      120,
      SIGNIN_KEEP_DEFAULT.idleMinutes,
    ),
    maxHours: bounded(
      env['HARNESS_SIGNIN_MAX_HOURS'],
      1,
      24,
      SIGNIN_KEEP_DEFAULT.maxHours,
    ),
  };
}

export type ClaudeSignInState =
  'not_allowed' | 'available' | 'connected' | 'disconnected';

/** The person's view (My AI accounts). */
export interface EngineSignInView {
  claude: {
    state: ClaudeSignInState;
    /** The person's switch: use their Claude account for their own tasks. */
    enabled: boolean;
    /**
     * Why it is not allowed: this server cannot carry a sign-in (no live worker advertises `vendor_signin`, the
     * sign-in add-on is off; SIGNGAP), the org's switch, or the person is a service account.
     */
    reason?:
      | 'engine_signin_unavailable'
      | 'engine_signin_off'
      | 'account_requires_interactive';
    email?: string;
    plan?: string;
    workPlan?: boolean;
    signedInAt?: string;
    disconnectedAt?: string;
    /** Whether the org admits consumer plans (Pro, Max). */
    consumerPlans: boolean;
    /** How long a sign-in lasts in a conversation on this deployment (SIGNIN-5). */
    keep: SignInKeep;
  };
  /** ChatGPT in Codex waits for OpenAI's written yes (plan §1.2): shown, never usable. */
  chatgpt: { state: 'waiting_vendor' };
}

/** What a describe of an open sign-in answers the owner. */
export interface SignInAttemptView {
  attemptId: string;
  runId: string;
  sessionId: string;
  state: 'waiting' | 'signed_in' | 'failed' | 'cancelled' | 'expired';
  /** Present while it waits: Anthropic's page, its host checked by the worker against the signed list. */
  link?: string;
  expiresAt: string;
}

/** The result of the pasted code. */
export interface SignInCodeResult {
  state: 'signed_in' | 'failed';
  reasonCode?: string;
  /** The code did not work and the login waits on (a new link when Claude Code restarted it). */
  retry: boolean;
  email?: string;
  plan?: string;
}

/** Who ended a sign-in, as the audit and the worker's log name it. */
export type SignOutBy = 'user' | 'admin' | 'erasure' | 'policy' | 'shared';
