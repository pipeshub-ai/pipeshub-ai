/**
 * U2 RC-1..RC-4: every reason a row or an admission refusal can carry reads
 * as a sentence in all ten locales, and a row's availability is decided once.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  accountKindKey,
  cloudAccountAvailability,
  isCloudProviderUsable,
  kindAvailability,
  offeringKey,
  providerName,
  REASON_CODES,
  reasonKey,
  rowAvailability,
} from '../access-copy';
import { ACCOUNT_KINDS, type AccessRule, type ModelAccessView } from '../types';

const LOCALES = join(__dirname, '..', '..', '..', '..', '..', 'lib', 'i18n', 'locales');
const catalogues = readdirSync(LOCALES)
  .filter((file) => file.endsWith('.json'))
  .map((file) => [file, JSON.parse(readFileSync(join(LOCALES, file), 'utf8'))] as const);

const lookup = (tree: unknown, key: string): unknown =>
  key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), tree);

const rule = (over: Partial<AccessRule> = {}): AccessRule => ({
  id: 'openai-api-key-user',
  provider: 'openai',
  accountKind: 'user_api_key',
  route: 'gateway',
  engines: null,
  status: 'allowed',
  consumer: false,
  source: '',
  built: true,
  outcome: 'allowed',
  ...over,
});

const view = (rules: AccessRule[]): ModelAccessView => ({
  version: 'v',
  offering: 'managed',
  policy: { disabledRules: [], consumerPlans: false },
  rules,
});

// A1's refusal codes (Node `AccessRefusalCode`) and the row outcomes (`RowOutcome`).
const ADMISSION_CODES = [
  'vendor_rules_invalid',
  'vendor_rule_missing',
  'vendor_prohibited',
  'vendor_pending',
  'offering_not_permitted',
  'org_policy_refused',
  'access_policy_unavailable',
  'account_not_owned',
  'account_missing',
  'account_provider_mismatch',
  'account_requires_engine',
  'account_model_required',
  'route_unavailable',
  'route_not_configured',
  'access_policy_invalid',
];

describe('access copy', () => {
  it('RC-1: there are ten locales', () => {
    expect(catalogues).toHaveLength(10);
  });

  it.each(catalogues)('RC-1: every reason, kind and offering has copy in %s', (_file, catalogue) => {
    const keys = [
      ...REASON_CODES.map(reasonKey),
      ...ADMISSION_CODES.map(reasonKey),
      ...ACCOUNT_KINDS.map(accountKindKey),
      ...['oss', 'managed', 'anything'].map(offeringKey),
      ...['gateway', 'vendor_injected', 'vendor_sandbox_login'].map((r) => `aiAccounts.routes.${r}`),
    ];
    for (const key of keys) {
      const value = lookup(catalogue, key);
      expect(typeof value === 'string' && value.length > 0, key).toBe(true);
    }
  });

  it('RC-1: every admission code is a known reason; an unknown code reads as the generic one', () => {
    for (const code of ADMISSION_CODES) expect(reasonKey(code)).toBe(`aiAccounts.reasons.${code}`);
    expect(reasonKey('something_new')).toBe('aiAccounts.reasons.unknown');
    expect(offeringKey('enterprise_plus')).toBe('aiAccounts.offerings.unknown');
  });

  it.each([
    ['allowed, built, a transport', rule(), 'available'],
    ['allowed, vendor-direct (not built)', rule({ route: 'vendor_injected', built: false }), 'route_not_built'],
    ['allowed, bedrock', rule({ provider: 'bedrock', accountKind: 'org_cloud' }), 'transport_not_built'],
    ['allowed, vertexai', rule({ provider: 'vertexai', accountKind: 'org_cloud' }), 'transport_not_built'],
    ['prohibited', rule({ status: 'prohibited', outcome: 'vendor_prohibited' }), 'vendor_prohibited'],
    ['pending', rule({ status: 'pending_vendor', outcome: 'vendor_pending' }), 'vendor_pending'],
    ['offering', rule({ outcome: 'offering_not_permitted' }), 'offering_not_permitted'],
    ['org switched off', rule({ outcome: 'org_policy_refused' }), 'org_policy_refused'],
    ['unknown', rule({ outcome: 'unknown' }), 'unknown'],
  ])('RC-2: %s', (_name, row, expected) => {
    expect(rowAvailability(row)).toBe(expected);
  });

  it('RC-3: a personal key the server reports as not built is not offered as a kind', () => {
    expect(kindAvailability('user_api_key', view([rule({ built: false })]))).toBe('route_not_built');
    expect(cloudAccountAvailability('bedrock', view([rule({ provider: 'bedrock', accountKind: 'org_cloud' })]))).toBe(
      'transport_not_built',
    );
  });

  it('RC-3: a kind is available when some generic row of it is', () => {
    const rules = [
      rule({ accountKind: 'org_cloud', provider: 'bedrock' }),
      rule({ accountKind: 'org_cloud', provider: 'azureopenai' }),
      rule({ accountKind: 'user_plan_injected', status: 'pending_vendor', outcome: 'vendor_pending', engines: ['codex'] }),
    ];
    expect(kindAvailability('org_cloud', view(rules))).toBe('available');
    expect(kindAvailability('user_plan_injected', view(rules))).toBe('vendor_rule_missing');
    expect(kindAvailability('org_api_key', view(rules))).toBe('vendor_rule_missing');
  });

  it('RC-4: one display name for both spellings of a provider', () => {
    expect(providerName('openAI')).toBe(providerName('openai'));
    expect(providerName('azureOpenAI')).toBe('Azure OpenAI');
    expect(providerName('vertexAI')).toBe(providerName('vertexai'));
    expect(providerName('someNewVendor')).toBe('someNewVendor');
    expect(isCloudProviderUsable('azureOpenAI')).toBe(true);
    expect(isCloudProviderUsable('bedrock')).toBe(false);
    expect(isCloudProviderUsable('vertexAI')).toBe(false);
  });
});
