import {
  ACCOUNT_KINDS,
  OFFERINGS,
  ROUTE_KINDS,
  RULE_STATUSES,
  type AccessAccountKind,
  type AccessRouteKind,
  type Offering,
  type VendorRuleStatus,
} from '../contracts/generated/core/enums';
import type { ModelAccessView } from '../contracts/generated/ext/pipeshub/api/model-access-view';
import type { RowOutcome } from '../contracts/generated/ext/pipeshub/enums';
import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';
import { ed25519Verifies, trustedReleaseKeys } from './release-keys';
import {
  VENDOR_RULE_EXTENSIONS,
  VENDOR_RULES_SIGNATURE_TEXT,
  VENDOR_RULES_TEXT,
} from './vendor-rules-release';

/**
 * Model access (A1, ARCHITECTURE §4.6): which account a run's model calls use,
 * by which route, under the vendors' terms. The terms are release data signed
 * with a release key compiled into the code (`vendor-rules-release.ts`, the
 * same bytes and key as Python's `harness_engines/vendor_rules.py`); an org can
 * only narrow them. `decideAccess` equals Python's `vendor_rules.decide`
 * (`contracts/harness/vectors/vendor-rules.json`).
 */

export { ACCOUNT_KINDS, OFFERINGS, ROUTE_KINDS, type Offering };
export type AccountKind = AccessAccountKind;
export type RouteKind = AccessRouteKind;
type RuleStatus = VendorRuleStatus;

export const VENDOR_RULES_SCHEMA = 'pipeshub.vendor-rules/1';
export const VENDOR_RULES_EXTENSION_SCHEMA =
  'pipeshub.vendor-rules-extension/1';
const EXTENSION_FIELDS = ['offering', 'rules', 'schema', 'version'];
const ROW_FIELDS = new Set([
  'id',
  'provider',
  'accountKind',
  'route',
  'engines',
  'offering',
  'status',
  'consumer',
  'source',
  'note',
]);
/**
 * OWD-8: model traffic goes through the Model Gateway; the one vendor-direct path is a person's own sign-in in
 * their sandbox (`vendor_sandbox_login`, OWD-17). A signed row that would send a key straight to the vendor is read
 * as prohibited whatever the document says (Python's `GATEWAY_BYPASS_ROUTES`), so no release reopens it by data.
 */
export const GATEWAY_BYPASS_ROUTES: ReadonlySet<RouteKind> = new Set([
  'vendor_injected',
]);

export interface VendorRule {
  id: string;
  provider: string;
  accountKind: AccountKind;
  route: RouteKind;
  /** Null: every engine (and our loop); otherwise only these, and the row is more specific. */
  engines: string[] | null;
  offering: Offering[];
  status: RuleStatus;
  /** A consumer plan's terms apply: off for an org until it opts in. */
  consumer: boolean;
  source: string;
}

export interface VendorRules {
  version: string;
  rules: VendorRule[];
}

export interface AccessCandidate {
  provider: string;
  accountKind: AccountKind;
  route: RouteKind;
  /** An engine kind, or `loop` for PipesHub's own loop. */
  engine: string;
}

/** What an org narrows: rows switched off; whether consumer-plan rows may be used. */
export type OrgAccessPolicy = ModelAccessView['policy'];

export const DEFAULT_ORG_ACCESS_POLICY: Readonly<OrgAccessPolicy> =
  Object.freeze({ disabledRules: [], consumerPlans: false });

/** The catalogue's `model_access` group in lower case (ERR-UX), as Python's access reason codes spell them. */
export type AccessRefusalCode = Lowercase<ErrorCodeOf<'model_access'>>;

export type VendorRulesInvalidDetail =
  'missing' | 'unsigned' | 'unknown_key' | 'bad_signature' | 'malformed';

/** A refused access decision: the code, the deciding row (if any) and, for invalid data, which way. */
export class ModelAccessRefusal extends Error {
  constructor(
    readonly code: AccessRefusalCode,
    message: string,
    readonly rule: string | null = null,
    readonly detail?: string,
  ) {
    super(message);
    this.name = 'ModelAccessRefusal';
  }
}

const invalid = (
  detail: VendorRulesInvalidDetail,
  message: string,
): ModelAccessRefusal =>
  new ModelAccessRefusal('vendor_rules_invalid', message, null, detail);

const isName = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0;

const names = (value: unknown, what: string): string[] => {
  if (!Array.isArray(value) || !value.every(isName)) {
    throw invalid('malformed', `${what} is not a list of names`);
  }
  return value;
};

const oneOf = <T extends string>(
  values: readonly T[],
  value: unknown,
): value is T => typeof value === 'string' && values.includes(value as T);

function parseRow(raw: unknown): VendorRule {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw invalid('malformed', 'a rule is not an object');
  }
  const row = raw as Record<string, unknown>;
  if (Object.keys(row).some((key) => !ROW_FIELDS.has(key))) {
    throw invalid('malformed', 'a rule has an unknown field');
  }
  const { id, provider, source, status, consumer, accountKind, route } = row;
  if (!isName(id) || !isName(provider) || !isName(source)) {
    throw invalid('malformed', "a rule's id, provider or source is empty");
  }
  if (
    !oneOf(ACCOUNT_KINDS, accountKind) ||
    !oneOf(ROUTE_KINDS, route) ||
    !oneOf(RULE_STATUSES, status) ||
    typeof consumer !== 'boolean'
  ) {
    throw invalid('malformed', 'a rule names an unknown kind or status');
  }
  const offering = names(row['offering'], 'offering');
  if (offering.length === 0 || !offering.every((o) => oneOf(OFFERINGS, o))) {
    throw invalid('malformed', 'a rule names an unknown offering');
  }
  const engines =
    row['engines'] === null || row['engines'] === undefined
      ? null
      : names(row['engines'], 'engines');
  if (engines !== null && engines.length === 0) {
    throw invalid('malformed', 'a rule names an empty engine list');
  }
  return {
    id,
    provider,
    accountKind,
    route,
    engines,
    offering: offering,
    status: GATEWAY_BYPASS_ROUTES.has(route) ? 'prohibited' : status,
    consumer,
    source,
  };
}

/** The rows of a verified document; anything that is not exactly a rule document is `malformed`. */
export function parseVendorRules(text: string): VendorRules {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw invalid('malformed', 'the vendor rules are not JSON');
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw invalid('malformed', 'the vendor rules are not an object');
  }
  const body = raw as Record<string, unknown>;
  if (
    body['schema'] !== VENDOR_RULES_SCHEMA ||
    typeof body['version'] !== 'string' ||
    !Array.isArray(body['rules'])
  ) {
    throw invalid(
      'malformed',
      'the vendor rules document has no schema, version or rules',
    );
  }
  const rules = (body['rules'] as unknown[]).map(parseRow);
  if (new Set(rules.map((rule) => rule.id)).size !== rules.length) {
    throw invalid('malformed', 'two vendor rules share an id');
  }
  return { version: body['version'], rules };
}

function signatureOf(text: string): { keyId: string; sig: Buffer } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw invalid('unsigned', 'the vendor rules signature is not JSON');
  }
  const body =
    typeof raw === 'object' && raw !== null
      ? (raw as Record<string, unknown>)
      : {};
  const { alg, keyId, sig } = body;
  if (alg !== 'Ed25519' || !isName(keyId) || !isName(sig)) {
    throw invalid(
      'unsigned',
      'the vendor rules signature is not an Ed25519 signature',
    );
  }
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(sig)) {
    throw invalid('unsigned', 'the vendor rules signature is not base64');
  }
  return { keyId, sig: Buffer.from(sig, 'base64') };
}

/**
 * `rules` with the offering a verified extension document adds (an edition's own
 * offering, x-extensible-enum); Python's `extend_vendor_rules`. It names one
 * offering no row names and the rows that offering may use, and opens only those:
 * no status changes and no row is added, so a core offering is never widened.
 */
export function extendVendorRules(
  rules: VendorRules,
  text: string,
): VendorRules {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw invalid('malformed', 'the vendor rules extension is not JSON');
  }
  if (
    typeof raw !== 'object' ||
    raw === null ||
    Array.isArray(raw) ||
    Object.keys(raw).sort().join(',') !== EXTENSION_FIELDS.join(',')
  ) {
    throw invalid(
      'malformed',
      'the vendor rules extension is not an extension document',
    );
  }
  const body = raw as Record<string, unknown>;
  const { version, offering } = body;
  if (body['schema'] !== VENDOR_RULES_EXTENSION_SCHEMA || !isName(version)) {
    throw invalid(
      'malformed',
      'the vendor rules extension has no schema or version',
    );
  }
  const named = new Set(rules.rules.flatMap((rule) => rule.offering));
  if (!oneOf(OFFERINGS, offering) || named.has(offering)) {
    throw invalid(
      'malformed',
      'the vendor rules extension names an unknown offering or one the rules decide',
    );
  }
  const ids = names(body['rules'], 'rules');
  const known = new Set(rules.rules.map((rule) => rule.id));
  if (
    ids.length === 0 ||
    new Set(ids).size !== ids.length ||
    !ids.every((id) => known.has(id))
  ) {
    throw invalid(
      'malformed',
      'the vendor rules extension names a rule the rules do not have',
    );
  }
  const opened = new Set(ids);
  return {
    version: `${rules.version}+${version}`,
    rules: rules.rules.map((rule) =>
      opened.has(rule.id)
        ? { ...rule, offering: [...rule.offering, offering] }
        : rule,
    ),
  };
}

/**
 * The rules, only when `signature` is a valid Ed25519 signature of `document`'s
 * UTF-8 bytes by one of `keys` (id → raw public key, base64). Every failure is
 * `vendor_rules_invalid` with its detail.
 */
export function verifyVendorRules(
  document: string | null,
  signature: string | null,
  keys: Readonly<Record<string, string>>,
): VendorRules {
  return parseVendorRules(verifiedDocument(document, signature, keys));
}

/** `document`, only when `signature` verifies it by one of `keys`; Python's `verify_release_document`. */
export function verifiedDocument(
  document: string | null,
  signature: string | null,
  keys: Readonly<Record<string, string>>,
): string {
  if (document === null)
    throw invalid('missing', 'the vendor rules are missing');
  if (signature === null) {
    throw invalid('unsigned', 'the vendor rules are not signed');
  }
  const { keyId, sig } = signatureOf(signature);
  const raw = Object.prototype.hasOwnProperty.call(keys, keyId)
    ? keys[keyId]
    : undefined;
  if (raw === undefined) {
    throw invalid(
      'unknown_key',
      'the vendor rules are signed by a key this release does not ship',
    );
  }
  if (!ed25519Verifies(document, raw, sig)) {
    throw invalid(
      'bad_signature',
      'the vendor rules signature does not verify',
    );
  }
  return document;
}

/** The shipped rules with each shipped extension, every document verified by `keys`. */
export function shippedVendorRules(
  keys: Readonly<Record<string, string>>,
  extensions: ReadonlyArray<{
    readonly text: string;
    readonly signature: string | null;
  }> = VENDOR_RULE_EXTENSIONS,
): VendorRules {
  return extensions.reduce(
    (rules, extension) =>
      extendVendorRules(
        rules,
        verifiedDocument(extension.text, extension.signature, keys),
      ),
    verifyVendorRules(VENDOR_RULES_TEXT, VENDOR_RULES_SIGNATURE_TEXT, keys),
  );
}

let release: { keys: string; rules: VendorRules } | null = null;

/**
 * The shipped rules, verified once per set of release keys in force (not
 * revoked, not past their not-after); a refusal is thrown again at every call,
 * and a key revoked or expired since is checked again (R41SUPPLY-FU-6).
 */
export function releaseVendorRules(): VendorRules {
  const keys = trustedReleaseKeys();
  const held = Object.keys(keys).sort().join(',');
  if (release?.keys !== held) {
    release = {
      keys: held,
      rules: shippedVendorRules(keys),
    };
  }
  return release.rules;
}

const matches = (rule: VendorRule, candidate: AccessCandidate): boolean =>
  rule.provider === candidate.provider &&
  rule.accountKind === candidate.accountKind &&
  rule.route === candidate.route &&
  (rule.engines === null || rule.engines.includes(candidate.engine));

/**
 * The row that allows `candidate`, or the refusal naming the deciding row.
 * Engine-specific rows win over generic ones; among the winners prohibited
 * beats pending beats allowed. The org policy only ever subtracts.
 */
export function decideAccess(
  rules: VendorRules,
  candidate: AccessCandidate,
  offering: Offering,
  policy: OrgAccessPolicy = DEFAULT_ORG_ACCESS_POLICY,
): VendorRule {
  const matching = rules.rules.filter((rule) => matches(rule, candidate));
  const specific = matching.filter((rule) => rule.engines !== null);
  const deciding = specific.length > 0 ? specific : matching;
  const first = deciding[0];
  if (first === undefined) {
    throw new ModelAccessRefusal(
      'vendor_rule_missing',
      'No vendor rule allows this account and route',
    );
  }
  const prohibited = deciding.find((rule) => rule.status === 'prohibited');
  if (prohibited !== undefined) {
    throw new ModelAccessRefusal(
      'vendor_prohibited',
      "The vendor's terms prohibit this account and route",
      prohibited.id,
    );
  }
  const pending = deciding.find((rule) => rule.status === 'pending_vendor');
  if (pending !== undefined) {
    throw new ModelAccessRefusal(
      'vendor_pending',
      'The vendor has not confirmed this account and route',
      pending.id,
    );
  }
  const allowed = deciding.find((rule) => rule.offering.includes(offering));
  if (allowed === undefined) {
    throw new ModelAccessRefusal(
      'offering_not_permitted',
      'This offering may not use this account and route',
      first.id,
    );
  }
  if (policy.disabledRules.includes(allowed.id)) {
    throw new ModelAccessRefusal(
      'org_policy_refused',
      'The organization switched this account and route off',
      allowed.id,
    );
  }
  if (allowed.consumer && !policy.consumerPlans) {
    throw new ModelAccessRefusal(
      'org_policy_refused',
      'The organization has not opted into consumer plans',
      allowed.id,
    );
  }
  return allowed;
}

/** The access a route pins (core RunSpec `ModelRoute.access`). */
export interface PinnedAccess {
  route: RouteKind;
  accountKind: AccountKind;
  /** The model account: `users/<userId>/<provider>` or `cloud/<accountId>`; absent: the org's configured key. */
  accountId?: string;
  pays: 'org' | 'user' | 'vendor_plan';
}

/**
 * What admission resolved for an engine run and the claim pins (store-only,
 * beside `engineProfile`): the concrete model, the configured model entry when
 * the org's key reaches it, and the access route.
 */
export interface PinnedModelAccess {
  route: { provider: string; model: string };
  model?: { modelKey: string; modelName: string };
  access: PinnedAccess;
}

/** A model account a run names: whose it is and its path id (Python `account_owner`). */
export type NamedAccount =
  | { kind: 'user_api_key'; userId: string; id: string; accountId: string }
  | { kind: 'org_cloud'; id: string; accountId: string };

const SEGMENT = /^[A-Za-z0-9._:@-]{1,128}$/;
const isSegment = (value: string | undefined): value is string =>
  value !== undefined && SEGMENT.test(value) && value !== '.' && value !== '..';

/** `users/<userId>/<provider>` or `cloud/<id>`; null for anything else. */
export function namedAccount(accountId: string): NamedAccount | null {
  const [root, first, second, ...rest] = accountId.split('/');
  if (
    root === 'users' &&
    isSegment(first) &&
    isSegment(second) &&
    rest.length === 0
  ) {
    return { kind: 'user_api_key', userId: first, id: second, accountId };
  }
  if (root === 'cloud' && isSegment(first) && second === undefined) {
    return { kind: 'org_cloud', id: first, accountId };
  }
  return null;
}

/**
 * Configured (`aiModels`) providers reached through an org cloud account rather
 * than an API key; Python's `ACCOUNT_KINDS` classifies them the same way.
 */
const ORG_CLOUD_PROVIDERS = new Set(['bedrock', 'vertexAI']);

export const configuredAccountKind = (provider: string): AccountKind =>
  ORG_CLOUD_PROVIDERS.has(provider) ? 'org_cloud' : 'org_api_key';

/** Who pays for a call on each account kind. */
export const payerOf = (kind: AccountKind): PinnedAccess['pays'] =>
  kind === 'user_api_key'
    ? 'user'
    : kind === 'org_api_key' || kind === 'org_cloud'
      ? 'org'
      : 'vendor_plan';

/**
 * One org's entry. An extension that keeps members of its own here declares
 * them by merging this interface and reads them through `extendModelAccessEntry`.
 */
export interface ModelAccessEntry {
  disabledRules: string[];
  consumerPlans: boolean;
  updatedAt: number;
  updatedBy: string;
}

/** `/services/agentHarness/modelAccess`: each org's narrowing, written by an org admin. */
export interface ModelAccessDocument {
  orgs: Record<string, ModelAccessEntry>;
}

export type { RowOutcome };

export function rowOutcome(
  rule: VendorRule,
  offering: Offering,
  policy: OrgAccessPolicy,
): RowOutcome {
  if (rule.status === 'prohibited') return 'vendor_prohibited';
  if (rule.status === 'pending_vendor') return 'vendor_pending';
  if (!rule.offering.includes(offering)) return 'offering_not_permitted';
  if (policy.disabledRules.includes(rule.id)) return 'org_policy_refused';
  if (rule.consumer && !policy.consumerPlans) return 'org_policy_refused';
  return 'allowed';
}

/** The gateway route is built; vendor-direct routes are rules, not paths, but for the rows listed below. */
export const BUILT_ROUTES: ReadonlySet<RouteKind> = new Set(['gateway']);
/** Vendor-direct rows this release builds: a person's own Claude account inside Claude Code (SIGNIN, VR-10). */
export const BUILT_ROWS: ReadonlySet<string> = new Set([
  'anthropic-claude-plan-claude-code',
]);

/** Whether a row's route is a path this release runs. */
export const rowBuilt = (rule: Pick<VendorRule, 'id' | 'route'>): boolean =>
  BUILT_ROUTES.has(rule.route) || BUILT_ROWS.has(rule.id);
