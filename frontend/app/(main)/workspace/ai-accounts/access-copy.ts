import { OFFERINGS } from '@/lib/contracts/harness/generated/core/enums';
import { RELEASE_ENGINE_NAMES } from '@/lib/contracts/harness/generated/core/policy-data';
import type { AccessRule, AccountKind, ModelAccessView } from './types';

/** Providers of an org cloud account (kind `org_cloud`). */
export const CLOUD_PROVIDERS = ['azureOpenAI', 'bedrock', 'vertexAI'] as const;
export type CloudProvider = (typeof CLOUD_PROVIDERS)[number];

// Product names are not translated.
const PROVIDER_NAMES: Readonly<Record<string, string>> = {
  openai: 'OpenAI',
  azureopenai: 'Azure OpenAI',
  openaicompatible: 'OpenAI-compatible',
  anthropic: 'Anthropic',
  gemini: 'Gemini',
  bedrock: 'Amazon Bedrock',
  vertexai: 'Vertex AI',
  foundry: 'Microsoft Foundry',
};

/** Account providers are camel case (`openAI`), vendor rows lower case (`openai`): one name for both. */
export const providerKey = (provider: string): string => provider.toLowerCase();

export const providerName = (provider: string): string => PROVIDER_NAMES[providerKey(provider)] ?? provider;

/**
 * Providers the vendor rules allow on the gateway but the model gateway has no
 * transport for yet (A1 D-7: SigV4 signing, Google service-account tokens).
 */
const NO_TRANSPORT = new Set(['bedrock', 'vertexai', 'foundry']);

export const isCloudProviderUsable = (provider: string): boolean => !NO_TRANSPORT.has(providerKey(provider));

/** Every reason the pages can show, each a key under `aiAccounts.reasons`. */
export const REASON_CODES = [
  'available',
  'vendor_prohibited',
  'vendor_pending',
  'offering_not_permitted',
  'org_policy_refused',
  'route_not_built',
  'transport_not_built',
  'vendor_rule_missing',
  'vendor_rules_invalid',
  'access_policy_unavailable',
  'access_policy_invalid',
  'account_not_owned',
  'account_missing',
  'account_provider_mismatch',
  'account_requires_engine',
  'account_model_required',
  'route_unavailable',
  'route_not_configured',
  'engine_unavailable',
  'engine_profile_untrusted',
  'engine_sandbox_missing',
  'engine_profile_widening',
  'unknown',
] as const;
export type ReasonCode = (typeof REASON_CODES)[number];

/** The translation key of a reason; a code this build does not know reads as the generic one. */
export function reasonKey(code: string): string {
  const known = (REASON_CODES as readonly string[]).includes(code) ? code : 'unknown';
  return `aiAccounts.reasons.${known}`;
}

export const accountKindKey = (kind: AccountKind): string => `aiAccounts.kinds.${kind}`;

export const offeringKey = (offering: string): string =>
  (OFFERINGS as readonly string[]).includes(offering)
    ? `aiAccounts.offerings.${offering}`
    : 'aiAccounts.offerings.unknown';

/** Whether a vendor row can be used here and now, and if not, the one reason shown. */
export function rowAvailability(rule: AccessRule): ReasonCode {
  if (rule.outcome !== 'allowed') return rule.outcome;
  if (!rule.built) return 'route_not_built';
  if (NO_TRANSPORT.has(providerKey(rule.provider))) return 'transport_not_built';
  return 'available';
}

/**
 * The gateway rows for one provider and account kind, most usable first.
 * Engine-specific rows are skipped: they refine a generic row for one engine.
 */
function kindRows(view: ModelAccessView, provider: string, kind: AccountKind): AccessRule[] {
  return view.rules.filter(
    (rule) =>
      rule.accountKind === kind &&
      rule.route === 'gateway' &&
      rule.engines === null &&
      providerKey(rule.provider) === providerKey(provider),
  );
}

function best(rows: AccessRule[]): ReasonCode {
  if (rows.length === 0) return 'vendor_rule_missing';
  const reasons = rows.map(rowAvailability);
  return reasons.includes('available') ? 'available' : reasons[0];
}

/** Can an org cloud account of this provider be used, and if not, why. */
export const cloudAccountAvailability = (provider: string, view: ModelAccessView): ReasonCode =>
  best(kindRows(view, provider, 'org_cloud'));

/** An account kind is offered when some generic gateway row of that kind is available for some provider. */
export function kindAvailability(kind: AccountKind, view: ModelAccessView): ReasonCode {
  const rows = view.rules.filter((rule) => rule.accountKind === kind && rule.engines === null);
  return best(rows);
}

// Product names are not translated.
const ENGINE_NAMES: Readonly<Record<string, string>> = {
  opencode: 'opencode',
  codex: 'Codex',
  claude_code: 'Claude Code',
};

const ACP_KIND = /^acp-[a-z][a-z0-9_-]{0,31}$/;

/** An ACP agent run as a coding engine (`acp-<agent>`, ACP-driver). */
export const isAcpEngine = (kind: string): boolean => ACP_KIND.test(kind);

/** A coding engine's product name (ENGPICK); an ACP agent's is its release entry's, also a product name. */
export const engineName = (kind: string): string => ENGINE_NAMES[kind] ?? RELEASE_ENGINE_NAMES[kind] ?? kind;
