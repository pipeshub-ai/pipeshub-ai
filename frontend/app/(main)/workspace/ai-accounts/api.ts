import { apiClient } from '@/lib/api';
import type { ProcessedError } from '@/lib/api';
import {
  ACCOUNT_KINDS,
  ROW_OUTCOMES,
  type AccessRule,
  type AccountKind,
  type AccountsError,
  type CodingEngineOption,
  type CodingEngineView,
  type ModelAccessView,
  type ModelChoice,
  type OrgAccessPolicy,
  type RowOutcome,
  type RuleStatus,
} from './types';

const HARNESS = '/api/v1/agent-harness';
const ACCESS = `${HARNESS}/model-accounts/access`;
const CODING_ENGINE = `${HARNESS}/model-accounts/coding-engine`;
const ORG_CODING_ENGINE = `${HARNESS}/admin/coding-engine`;

// These pages render every failure themselves ("try again", "admins only", the field at fault).
const QUIET = { suppressErrorToast: true } as const;

const ADMIN_REQUIRED = 'Admin access required';

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const isAccountKind = (value: unknown): value is AccountKind =>
  typeof value === 'string' && (ACCOUNT_KINDS as readonly string[]).includes(value);

const toStatus = (value: unknown): RuleStatus =>
  value === 'allowed' || value === 'prohibited' || value === 'pending_vendor' ? value : 'unknown';

const toOutcome = (value: unknown): RowOutcome =>
  typeof value === 'string' && (ROW_OUTCOMES as readonly string[]).includes(value)
    ? (value as RowOutcome)
    : 'unknown';

function toRule(value: unknown): AccessRule | null {
  if (!isObject(value)) return null;
  const id = text(value.id);
  const provider = text(value.provider);
  const route = text(value.route);
  if (!id || !provider || !route || !isAccountKind(value.accountKind)) return null;
  return {
    id,
    provider,
    accountKind: value.accountKind,
    route,
    engines: Array.isArray(value.engines)
      ? value.engines.filter((e): e is string => typeof e === 'string')
      : null,
    status: toStatus(value.status),
    consumer: value.consumer === true,
    source: text(value.source) ?? '',
    built: value.built === true,
    outcome: toOutcome(value.outcome),
  };
}

function toPolicy(value: unknown): OrgAccessPolicy {
  if (!isObject(value)) return { disabledRules: [], consumerPlans: false };
  return {
    disabledRules: Array.isArray(value.disabledRules)
      ? value.disabledRules.filter((id): id is string => typeof id === 'string')
      : [],
    consumerPlans: value.consumerPlans === true,
  };
}

const price = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

function toModelChoice(value: unknown): ModelChoice | null {
  if (!isObject(value)) return null;
  const modelKey = text(value.modelKey);
  const modelName = text(value.modelName);
  const provider = text(value.provider);
  if (!modelKey || !modelName || !provider) return null;
  return { modelKey, modelName, provider, usdPerMTok: price(value.usdPerMTok), allowed: value.allowed === true };
}

function toRoleCostCeilings(value: unknown): Record<string, number> {
  const ceilings: Record<string, number> = {};
  for (const [role, ceiling] of Object.entries(isObject(value) ? value : {})) {
    const usd = price(ceiling);
    if (usd !== null) ceilings[role] = usd;
  }
  return ceilings;
}

export function toAccessView(data: unknown): ModelAccessView {
  const view = isObject(data) ? data : {};
  return {
    version: text(view.version) ?? '',
    offering: text(view.offering) ?? '',
    policy: toPolicy(view.policy),
    rules: (Array.isArray(view.rules) ? view.rules : [])
      .map(toRule)
      .filter((r): r is AccessRule => r !== null),
    ...(Array.isArray(view.modelChoices)
      ? { modelChoices: view.modelChoices.map(toModelChoice).filter((c): c is ModelChoice => c !== null) }
      : {}),
    ...(isObject(view.roleCostCeilings) ? { roleCostCeilings: toRoleCostCeilings(view.roleCostCeilings) } : {}),
  };
}

function toCodingEngineOption(value: unknown): CodingEngineOption | null {
  if (!isObject(value)) return null;
  const kind = text(value.kind);
  if (!kind) return null;
  // The finer reason, when the server gives one, is what the page shows (`engine_profile_untrusted`, ADR-SEC-01 D7).
  const code = isObject(value.reason) ? (text(value.reason.reasonCode) ?? text(value.reason.code)) : null;
  return { kind, available: value.available === true, reason: code === null ? null : code.toLowerCase() };
}

export function toCodingEngineView(data: unknown): CodingEngineView {
  const view = isObject(data) ? data : {};
  return {
    engines: (Array.isArray(view.engines) ? view.engines : [])
      .map(toCodingEngineOption)
      .filter((engine): engine is CodingEngineOption => engine !== null),
    orgDefault: text(view.orgDefault),
    choice: text(view.choice) ?? 'org_default',
    withoutGvisor: view.withoutGvisor === true,
    gvisorSyscallFilter: typeof view.gvisorSyscallFilter === 'boolean' ? view.gvisorSyscallFilter : null,
    // A server older than DEEPROUTE does not say: it never hands off on its own, so the switch reads off there.
    autoRoute: view.autoRoute === true,
  };
}

/**
 * A coding-engine save refused: the server's own sentence for a typed refusal
 * (an engine the org does not allow or cannot use now), never "admins only".
 */
function toCodingEngineError(error: unknown): AccountsError {
  const mapped = toAccountsError(error, true);
  const processed = error as Partial<ProcessedError> | null;
  const message = typeof processed?.message === 'string' ? processed.message : null;
  if ((mapped.kind === 'forbidden' || mapped.kind === 'conflict') && message !== null && message !== ADMIN_REQUIRED) {
    return { kind: 'invalid', status: mapped.status, message };
  }
  return mapped;
}

/**
 * A failure as the pages show it. `userAdminCheck` answers a non-admin 400
 * (P2.4b FB-9), so a 400 on an admin read means "not an admin", while a 400
 * on a write is the field the server refused, in its own words.
 */
export function toAccountsError(error: unknown, write = false): AccountsError {
  const processed = error as Partial<ProcessedError> | null;
  const status = typeof processed?.statusCode === 'number' ? processed.statusCode : null;
  const message = typeof processed?.message === 'string' ? processed.message : null;
  if (status === null || status === 503 || status === 502 || status === 504) {
    return { kind: 'unavailable', status, message: null };
  }
  if (status === 403 || (status === 400 && message === ADMIN_REQUIRED)) {
    return { kind: 'forbidden', status, message: null };
  }
  if (status === 404) return { kind: 'not_found', status, message: null };
  if (status === 409 && write) return { kind: 'conflict', status, message: null };
  if ((status === 400 || status === 422) && write) return { kind: 'invalid', status, message };
  return { kind: 'failed', status, message: null };
}

async function call<T>(run: () => Promise<T>, write = false): Promise<T> {
  try {
    return await run();
  } catch (error) {
    throw toAccountsError(error, write);
  }
}

export const isAccountsError = (value: unknown): value is AccountsError =>
  isObject(value) &&
  typeof value.kind === 'string' &&
  ['unavailable', 'forbidden', 'invalid', 'not_found', 'conflict', 'failed'].includes(value.kind);

export const ModelAccountsApi = {
  access: () => call(async () => toAccessView((await apiClient.get(ACCESS, QUIET)).data)),

  codingEngine: () => call(async () => toCodingEngineView((await apiClient.get(CODING_ENGINE, QUIET)).data)),

  putCodingEngine: async (choice: string) => {
    try {
      return toCodingEngineView((await apiClient.put(CODING_ENGINE, { choice }, QUIET)).data);
    } catch (error) {
      throw toCodingEngineError(error);
    }
  },

  putOrgCodingEngine: async (defaultEngine: string | null) => {
    try {
      return toCodingEngineView((await apiClient.put(ORG_CODING_ENGINE, { defaultEngine }, QUIET)).data);
    } catch (error) {
      throw toCodingEngineError(error);
    }
  },

  /** DEEPROUTE: the org's "Hand long work to the coding engine" switch. */
  putOrgAutoRoute: async (autoRoute: boolean) => {
    try {
      return toCodingEngineView((await apiClient.put(ORG_CODING_ENGINE, { autoRoute }, QUIET)).data);
    } catch (error) {
      throw toCodingEngineError(error);
    }
  },
};
