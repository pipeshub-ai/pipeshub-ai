/**
 * The OAuth app and token repositories and the lookups made before an org is known, on PostgreSQL (Alembic head, row-level
 * security, through PgBouncer): the same contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { PgOAuthAppRepository } from '../../src/libs/repositories/oauth-app/pg';
import { PgOAuthTokenRepository } from '../../src/libs/repositories/oauth-token/pg';
import { PgIdentityLookup } from '../../src/libs/repositories/identity-lookup/pg';
import {
  OAuthDeviceCodeStatus,
  type OAuthTokenRepository,
} from '../../src/libs/repositories/oauth-token';
import type { IdentityLookup } from '../../src/libs/repositories/identity-lookup';
import { describeOAuthAppContract } from './contract/oauth-app.contract';
import { describeOAuthTokenContract } from './contract/oauth-token.contract';
import {
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  scoped,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

/** A stored row's key names: the columns that are always present, plus the optional ones that are not NULL. */
export const keysOf = (
  row: Record<string, unknown>,
  always: string[],
  optional: Array<[string, string]>,
): string[] => [
  ...always,
  ...optional.filter(([col]) => row[col] !== null).map(([, key]) => key),
];

const camel = (s: string): string =>
  s.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
/** A stored row the way a document read: camel-case keys, no key for NULL. */
export const documentOf = (
  row: Record<string, unknown> | undefined,
): Record<string, unknown> | null =>
  row
    ? Object.fromEntries(
        Object.entries(row)
          .filter(([, v]) => v !== null)
          .map(([k, v]) => [k === 'id' ? '_id' : camel(k), v]),
      )
    : null;

const APP_ALWAYS = [
  '_id',
  'slug',
  'clientId',
  'clientSecretEncrypted',
  'name',
  'orgId',
  'createdBy',
  'redirectUris',
  'allowedGrantTypes',
  'allowedScopes',
  'status',
  'isConfidential',
  'isDynamic',
  'accessTokenLifetime',
  'refreshTokenLifetime',
  'isDeleted',
  'createdAt',
  'updatedAt',
  '__v',
];
const APP_OPTIONAL: Array<[string, string]> = [
  ['description', 'description'],
  ['token_identity_user_id', 'tokenIdentityUserId'],
  ['logo_url', 'logoUrl'],
  ['homepage_url', 'homepageUrl'],
  ['privacy_policy_url', 'privacyPolicyUrl'],
  ['terms_of_service_url', 'termsOfServiceUrl'],
  ['deleted_by', 'deletedBy'],
];
const ACCESS_ALWAYS = [
  '_id',
  'tokenHash',
  'clientId',
  'orgId',
  'scopes',
  'expiresAt',
  'isRevoked',
  'createdAt',
  'updatedAt',
  '__v',
];
const ACCESS_OPTIONAL: Array<[string, string]> = [
  ['user_id', 'userId'],
  ['revoked_at', 'revokedAt'],
  ['revoked_by', 'revokedBy'],
  ['revoked_reason', 'revokedReason'],
  ['parent_refresh_token_id', 'parentRefreshTokenId'],
  ['name', 'name'],
  ['last_used_at', 'lastUsedAt'],
  ['actor_user_id', 'actorUserId'],
];

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL OAuth stores (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    const ledger = new OrgLedger();

    before(async () => {
      stack = await startPgStack(bin, 'oauth_stores');
      ledger.learn({ orgId: newId() });
    });
    after(async () => {
      await stack?.stop();
    });

    const row = (
      table: string,
      id: string,
    ): Promise<Record<string, unknown> | undefined> =>
      stack.admin(
        async (c) =>
          (await c.query(`SELECT * FROM ${table} WHERE id = $1`, [id]))
            .rows[0] as Record<string, unknown> | undefined,
      );

    const rawApps = new PgOAuthAppRepository();
    const scopedApps = scoped(rawApps, ledger, {
      findOwned: 0,
      listOwned: 0,
      updateOwned: 0,
      listClientIdsCreatedBy: 0,
      softDeleteCreatedBy: 0,
    });
    // the ledger learns hex ids only; a client id is not one, so these calls run in the org that holds the client
    const BY_CLIENT = new Set([
      'findLiveByClientId',
      'findLiveAllowedScopes',
      'findLiveIdentity',
      'findByClientId',
    ]);
    const appRepo = new Proxy(scopedApps, {
      get(target, prop, receiver) {
        if (typeof prop !== 'string' || !BY_CLIENT.has(prop))
          return Reflect.get(target, prop, receiver) as unknown;
        return async (clientId: string) => {
          const org = await stack.admin(
            async (c) =>
              (
                await c.query(
                  'SELECT org_id FROM oauth.apps WHERE client_id = $1',
                  [clientId],
                )
              ).rows[0]?.org_id as string | undefined,
          );
          const call = (
            rawApps as unknown as Record<
              string,
              (c: string) => Promise<unknown>
            >
          )[prop]!;
          return org
            ? inOrg(org, () => call.call(rawApps, clientId))
            : (
                target as unknown as Record<
                  string,
                  (c: string) => Promise<unknown>
                >
              )[prop]!(clientId);
        };
      },
    });

    describe('OAuthAppRepository contract', () => {
      describeOAuthAppContract({
        repo: appRepo,
        newId,
        rawKeys: async (id) =>
          keysOf((await row('oauth.apps', id))!, APP_ALWAYS, APP_OPTIONAL),
        setCreatedAt: (id, at) =>
          stack.admin(
            async (c) => { await c.query(
                'UPDATE oauth.apps SET created_at = $2 WHERE id = $1',
                [id, at],
              ) },
          ),
      });
    });

    describe('OAuthTokenRepository and IdentityLookup contract', () => {
      const raw = new PgOAuthTokenRepository();
      const tokens = scoped(raw, ledger);
      // an approval takes its user and org from the unit: run it as the person it names
      const approvals = new Proxy(tokens, {
        get(target, prop, receiver) {
          if (prop !== 'decideDevice')
            return Reflect.get(target, prop, receiver) as unknown;
          return (
            id: string,
            d: Parameters<OAuthTokenRepository['decideDevice']>[1],
          ) =>
            d.status === OAuthDeviceCodeStatus.APPROVED
              ? inOrg(d.orgId, () => raw.decideDevice(id, d), d.userId)
              : target.decideDevice(id, d);
        },
      });
      const lookup: IdentityLookup = scoped(new PgIdentityLookup(), ledger);
      describeOAuthTokenContract({
        tokens: approvals,
        lookup,
        apps: appRepo,
        newId,
        rawAccessKeys: async (id) =>
          keysOf(
            (await row('oauth.access_tokens', id))!,
            ACCESS_ALWAYS,
            ACCESS_OPTIONAL,
          ),
        rawAccess: async (id) =>
          documentOf(await row('oauth.access_tokens', id)),
        rawRefresh: async (id) =>
          documentOf(await row('oauth.refresh_tokens', id)),
        rawDevice: async (id) =>
          documentOf(await row('oauth.device_authorizations', id)),
        setAccessCreatedAt: (id, at) =>
          stack.admin(
            async (c) => { await c.query(
                'UPDATE oauth.access_tokens SET created_at = $2 WHERE id = $1',
                [id, at],
              ) },
          ),
      });
    });
  });
}
