/**
 * The OAuth app and token repositories and the lookups made before an org is known, on PostgreSQL (Alembic head, row-level
 * security, through PgBouncer): the same contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createHash } from 'crypto';
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

    describe('device approval (PostgreSQL only: MongoDB overwrites a decided request)', () => {
      const raw = new PgOAuthTokenRepository();
      const tokens = scoped(raw, ledger);
      const orgId = newId();
      const device = async () => {
        ledger.learn({ orgId });
        const input = {
          deviceCodeHash: createHash('sha256').update(`dc-${newId()}`).digest('hex'),
          userCode: newId().slice(-12).toUpperCase(),
          clientId: 'c',
          scopes: ['a'],
          interval: 5,
          expiresAt: new Date(Date.now() + 600_000),
        };
        await tokens.createDevice(input);
        return (await tokens.findDeviceByUserCode(input.userCode))!;
      };
      const approve = (id: string, userId: string) =>
        inOrg(orgId, () => raw.decideDevice(id, { status: OAuthDeviceCodeStatus.APPROVED, userId, orgId }), userId);

      it('approves a pending request, lets the same person confirm again, and refuses anyone after that or after a denial', async () => {
        const first = await device();
        const alice = newId();
        await approve(first._id, alice);
        await approve(first._id, alice);
        let failure: Error | undefined;
        try {
          await approve(first._id, newId());
        } catch (e) {
          failure = e as Error;
        }
        expect(failure?.message).to.match(/can no longer be approved/);
        const denied = await device();
        await tokens.decideDevice(denied._id, { status: OAuthDeviceCodeStatus.DENIED });
        failure = undefined;
        try {
          await approve(denied._id, alice);
        } catch (e) {
          failure = e as Error;
        }
        expect(failure?.message).to.match(/can no longer be approved/);
      });
    });

    describe('the org of a refresh grant (PostgreSQL only: MongoDB needs no org)', () => {
      const raw = new PgOAuthTokenRepository();
      const tokens = scoped(raw, ledger);
      const lookup: IdentityLookup = scoped(new PgIdentityLookup(), ledger);
      const issue = async (token: string, expiresAt: Date) => {
        const orgId = newId();
        const rt = await tokens.issueRefresh({
          tokenHash: createHash('sha256').update(token).digest('hex'),
          clientId: 'c',
          userId: newId(),
          orgId,
          scopes: ['a'],
          expiresAt,
        });
        return { orgId, rt };
      };

      it('names the org of a live, a rotated (replayed) and an expired token, and none for an unknown one', async () => {
        const live = await issue('live-refresh-token', new Date(Date.now() + 3600_000));
        expect(await lookup.resolveRefreshOrg('live-refresh-token')).to.equal(live.orgId);
        // a replay: the grant must run as the org that issued the token to be refused there
        expect(await tokens.rotateRefresh(live.rt._id)).to.equal(true);
        expect(await lookup.resolveRefresh(createHash('sha256').update('live-refresh-token').digest('hex'))).to.equal(null);
        expect(await lookup.resolveRefreshOrg('live-refresh-token')).to.equal(live.orgId);
        const old = await issue('old-refresh-token', new Date(Date.now() - 1000));
        expect(await lookup.resolveRefreshOrg('old-refresh-token')).to.equal(old.orgId);
        expect(await lookup.resolveRefreshOrg('no-such-token')).to.equal(null);
      });
    });
  });
}
