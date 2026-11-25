import { apiClient } from '@/lib/api';
import { RELEASE_DEFAULTS } from '@/lib/contracts/harness/generated/core/policy-data';
import { toAccountsError } from './api';

const HARNESS = '/api/v1/agent-harness';
const ME = `${HARNESS}/engine-accounts/me`;
const SIGNIN = `${HARNESS}/engine-accounts/signin`;
const QUIET = { suppressErrorToast: true } as const;

export type ClaudeSignInState = 'not_allowed' | 'available' | 'connected' | 'disconnected';

/** Why the person cannot use their Claude account: this server, the org's switch, or a service account. */
export type ClaudeSignInReason = 'engine_signin_unavailable' | 'engine_signin_off' | 'account_requires_interactive';

/**
 * Where an admin reads how to turn on the sign-in add-on (SIGNGAP): its section of the coding-engine install guide
 * this release ships (`policy/release.json` `engineIsolation.installGuide`, GVISOR.md).
 */
export const SIGNIN_ADDON_GUIDE = `${RELEASE_DEFAULTS.engineIsolation.installGuide}#personal-claude-accounts-the-sign-in-add-on`;

/** The person's own Claude account in Claude Code (SIGNIN): what Claude Code reported, never a token. */
export interface EngineSignInView {
  claude: {
    state: ClaudeSignInState;
    enabled: boolean;
    reason: ClaudeSignInReason | null;
    email: string | null;
    plan: string | null;
    workPlan: boolean | null;
    signedInAt: string | null;
    disconnectedAt: string | null;
    consumerPlans: boolean;
    keep: SignInKeep;
  };
  chatgpt: { state: 'waiting_vendor' };
}

/** How long a Claude sign-in lasts in a conversation on this deployment (SIGNIN-5). */
export interface SignInKeep {
  idleMinutes: number;
  maxHours: number;
}

/** The server's own defaults, used only when it says nothing. */
export const SIGNIN_KEEP_DEFAULT: SignInKeep = { idleMinutes: 30, maxHours: 12 };

export interface SignInAttemptView {
  attemptId: string;
  state: 'waiting' | 'signed_in' | 'failed' | 'cancelled' | 'expired';
  /** Anthropic's page, present only while it waits; shown only when `anthropicLink` passes. */
  link: string | null;
  expiresAt: string | null;
}

export interface SignInCodeResult {
  state: 'signed_in' | 'failed';
  reasonCode: string | null;
  retry: boolean;
  plan: string | null;
  email: string | null;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): string | null => (typeof value === 'string' && value.length > 0 ? value : null);

const CLAUDE_STATES: readonly ClaudeSignInState[] = ['not_allowed', 'available', 'connected', 'disconnected'];
const REASONS: readonly ClaudeSignInReason[] = ['engine_signin_unavailable', 'engine_signin_off', 'account_requires_interactive'];
const ATTEMPT_STATES: readonly SignInAttemptView['state'][] = ['waiting', 'signed_in', 'failed', 'cancelled', 'expired'];
/** The hosts Anthropic's sign-in page lives on (SIGNIN-1, SP-1); the worker checks the signed list first. */
const LINK_HOSTS: ReadonlySet<string> = new Set(['claude.com', 'claude.ai']);

/** A link the card may show: https, on Anthropic's sign-in hosts, no credentials or port in it. */
export function anthropicLink(link: string | null): string | null {
  if (link === null) return null;
  try {
    const url = new URL(link);
    return url.protocol === 'https:' && LINK_HOSTS.has(url.hostname) && !url.port && !url.username && !url.password
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

/** Field by field, so nothing else the server sends reaches a component. */
export function toEngineSignInView(data: unknown): EngineSignInView {
  const view = isObject(data) ? data : {};
  const claude = isObject(view.claude) ? view.claude : {};
  const state = CLAUDE_STATES.find((s) => s === claude.state) ?? 'not_allowed';
  const reason = REASONS.find((r) => r === claude.reason) ?? null;
  return {
    claude: {
      state,
      enabled: claude.enabled === true,
      reason: state === 'not_allowed' ? (reason ?? 'engine_signin_off') : reason,
      email: text(claude.email),
      plan: text(claude.plan),
      workPlan: typeof claude.workPlan === 'boolean' ? claude.workPlan : null,
      signedInAt: text(claude.signedInAt),
      disconnectedAt: text(claude.disconnectedAt),
      consumerPlans: claude.consumerPlans === true,
      keep: toKeep(claude.keep),
    },
    chatgpt: { state: 'waiting_vendor' },
  };
}

const whole = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : fallback;

/** How long a sign-in lasts on this deployment, as the server reports it (SIGNIN-5). */
export function toKeep(data: unknown): SignInKeep {
  const keep = isObject(data) ? data : {};
  return {
    idleMinutes: whole(keep.idleMinutes, SIGNIN_KEEP_DEFAULT.idleMinutes),
    maxHours: whole(keep.maxHours, SIGNIN_KEEP_DEFAULT.maxHours),
  };
}

export function toSignInAttempt(data: unknown): SignInAttemptView {
  const view = isObject(data) ? data : {};
  return {
    attemptId: text(view.attemptId) ?? '',
    state: ATTEMPT_STATES.find((s) => s === view.state) ?? 'failed',
    link: text(view.link),
    expiresAt: text(view.expiresAt),
  };
}

export function toSignInCodeResult(data: unknown): SignInCodeResult {
  const view = isObject(data) ? data : {};
  return {
    state: view.state === 'signed_in' ? 'signed_in' : 'failed',
    reasonCode: text(view.reasonCode),
    retry: view.retry === true,
    plan: text(view.plan),
    email: text(view.email),
  };
}

async function call<T>(run: () => Promise<T>, write = false): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw toAccountsError(error, write);
  }
}

const seg = encodeURIComponent;

export const EngineSignInApi = {
  me: () => call(async () => toEngineSignInView((await apiClient.get(ME, QUIET)).data)),

  setClaude: (enabled: boolean) =>
    call(async () => toEngineSignInView((await apiClient.put(`${ME}/claude`, { enabled }, QUIET)).data), true),

  disconnectClaude: () => call(async () => toEngineSignInView((await apiClient.delete(`${ME}/claude`, QUIET)).data), true),

  attempt: (attemptId: string) =>
    call(async () => toSignInAttempt((await apiClient.get(`${SIGNIN}/${seg(attemptId)}`, QUIET)).data)),

  /** The code goes straight to Claude Code in the person's sandbox; nothing here keeps it. */
  submitCode: (attemptId: string, code: string) =>
    call(
      async () => toSignInCodeResult((await apiClient.post(`${SIGNIN}/${seg(attemptId)}/code`, { code }, QUIET)).data),
      true,
    ),

  cancel: (attemptId: string) =>
    call(async () => {
      await apiClient.delete(`${SIGNIN}/${seg(attemptId)}`, QUIET);
    }, true),
};
