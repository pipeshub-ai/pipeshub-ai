/**
 * Refusals of the harness routes as the U4 screens render them: U2's kinds
 * (`unavailable`, `forbidden`, `invalid`, `not_found`, `failed`) plus the
 * route's machine code, which each screen maps to its own copy.
 */
import type { TFunction } from 'i18next';
import { toAccountsError } from '../ai-accounts/api';
import type { AccountsError } from '../ai-accounts/types';
import { isObject, text } from './pick';

export interface HarnessError extends AccountsError {
  /** `SCHEDULE_INVALID`, `runner_egress_invalid`, …: a relayed Python refusal's `reasonCode`, or Node's code without its `HTTP_AGENT_HARNESS_` prefix. */
  code: string | null;
}

const NODE_PREFIXES = ['HTTP_AGENT_HARNESS_', 'HTTP_'];

/** Node's error body, `{error: {code, message, reasonCode?}}`, from a rejected `apiClient` call. */
function nodeError(error: unknown): Record<string, unknown> | null {
  const original = isObject(error) && isObject(error.originalError) ? error.originalError : null;
  const response = original && isObject(original.response) ? original.response : null;
  const data = response && isObject(response.data) ? response.data : null;
  return data && isObject(data.error) ? data.error : null;
}

function refusalCode(error: unknown): string | null {
  const body = nodeError(error);
  const relayed = text(body?.reasonCode);
  if (relayed !== null) return relayed;
  const code = text(body?.code);
  if (code === null) return null;
  const prefix = NODE_PREFIXES.find((p) => code.startsWith(p));
  return prefix === undefined ? code : code.slice(prefix.length);
}

export function toHarnessError(error: unknown, write = false): HarnessError {
  return { ...toAccountsError(error, write), code: refusalCode(error) };
}

export const isHarnessError = (value: unknown): value is HarnessError =>
  isObject(value) && typeof value.kind === 'string' && 'code' in value;

/** Runs a call and rethrows any failure as a `HarnessError`. */
export async function harnessCall<T>(run: () => Promise<T>, write = false): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw toHarnessError(error, write);
  }
}

/** The sentence for a refusal: the feature's copy for its code, else the generic copy (and, on a write, the server's words). */
export function refusalText(t: TFunction, copy: Readonly<Record<string, string>>, error: HarnessError): string {
  const key = error.code !== null ? copy[error.code] : undefined;
  if (key !== undefined) {
    return error.kind === 'invalid' && error.message ? `${t(key)} ${error.message}` : t(key);
  }
  if (error.kind === 'invalid' && error.message) return error.message;
  switch (error.kind) {
    case 'unavailable':
      return t('aiAccounts.common.unavailable');
    case 'forbidden':
      return t('aiAccounts.common.forbidden');
    case 'not_found':
      return t('aiAccounts.common.notFound');
    default:
      return t('aiAccounts.common.failed');
  }
}

/** A fresh idempotency key (`[A-Za-z0-9-]`, what every harness route accepts). */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
