/**
 * What only PostgreSQL shows about the OAuth stores: the token endpoints have no session, so a call that holds a secret runs as
 * the install's organisation; a key of another org finds nothing; codes are stored hashed; the device flow is the organisation's
 * own table; the slug counter and the app are one unit.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createHash } from 'crypto';
import { MissingOrgContext } from '../../src/libs/context/orgContext';
import { UniqueConstraintError } from '../../src/libs/repositories/errors';
import { setIdentityPort } from '../../src/libs/repositories/identity';
import { PgIdentityPort } from '../../src/libs/repositories/identity/pg';
import { TtlSweeper } from '../../src/libs/services/ttl-sweeper.service';
import { PgIdentityLookup } from '../../src/libs/repositories/identity-lookup/pg';
import { PgUnitOfWork, kernelTx } from '../../src/libs/repositories/pg';
import { PgOAuthAppRepository } from '../../src/libs/repositories/oauth-app/pg';
import { PgOAuthTokenRepository } from '../../src/libs/repositories/oauth-token/pg';
import { OAuthDeviceCodeStatus } from '../../src/libs/repositories/oauth-token';
import { OAuthGrantType } from '../../src/libs/repositories/oauth-app';
import {
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;
const sha = (v: string): string => createHash('sha256').update(v).digest('hex');
const future = (): Date => new Date(Date.now() + 3_600_000);
const past = (): Date => new Date(Date.now() - 2 * 3_600_000);
let n = 0;
const uniq = (p: string): string => `${p}-${process.pid}-${Date.now()}-${n++}`;
const rejected = async (p: Promise<unknown>): Promise<Error> => {
  try {
    await p;
  } catch (e) {
    return e as Error;
  }
  throw new Error('expected the call to fail');
};

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `PostgreSQL OAuth stores, what only the database shows (${bin})`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      const apps = new PgOAuthAppRepository();
      const tokens = new PgOAuthTokenRepository();
      const lookup = new PgIdentityLookup();
      const A = newId();
      const B = newId();
      const U = newId();

      before(async () => {
        stack = await startPgStack(bin, 'oauth_checks', {
          pool: 4,
          clientPool: 24,
          jobs: true,
        });
        // the install's organisation: the one a call that holds only a secret runs as
        await stack.admin((c) =>
          c.query(
            `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, created_at, updated_at)
             VALUES ($1, 'example.com', 'a@example.com', 'business', now(), now())`,
            [A],
          ),
        );
        setIdentityPort(new PgIdentityPort());
      });
      after(async () => {
        setIdentityPort();
        await stack?.stop();
      });

      const app = (org: string, clientId = uniq('client')) =>
        apps.create({
          clientId,
          clientSecretEncrypted: 'e',
          name: 'n',
          orgId: org,
          createdBy: U,
          redirectUris: [],
          allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
          allowedScopes: ['a'],
          isConfidential: true,
        });
      const access = (
        org: string,
        clientId: string,
        over: Record<string, unknown> = {},
      ) => {
        const tokenHash = sha(uniq('at'));
        return tokens
          .issueAccess({
            tokenHash,
            clientId,
            userId: U,
            orgId: org,
            scopes: ['a'],
            expiresAt: future(),
            ...over,
          })
          .then((dto) => ({ dto, tokenHash }));
      };

      describe('calls that hold only a secret, with no session (the token endpoints)', () => {
        it('a client id, a token hash and a code lead to their org', async () => {
          const a = await app(A);
          expect((await apps.findLiveByClientId(a.clientId))?._id).to.equal(
            a._id,
          );
          expect(await lookup.resolveClient(a.clientId)).to.deep.equal({
            orgId: A,
            appId: a._id,
            status: 'active',
          });
          const { tokenHash } = await access(A, a.clientId);
          expect((await lookup.resolveToken(tokenHash))?.orgId).to.equal(A);
          const rh = sha(uniq('rt'));
          const rt = await tokens.issueRefresh({
            tokenHash: rh,
            clientId: a.clientId,
            userId: U,
            orgId: A,
            scopes: ['a'],
            expiresAt: future(),
          });
          expect((await tokens.findRefreshByHash(rh))?._id).to.equal(rt._id);
          const code = uniq('code');
          await tokens.createCode({
            code,
            clientId: a.clientId,
            userId: U,
            orgId: A,
            redirectUri: 'https://x.example/cb',
            scopes: ['a'],
            expiresAt: future(),
          });
          expect(await tokens.consumeCode(code, a.clientId)).to.equal(true);
          expect(
            await tokens.revokeAccessByHash(tokenHash, a.clientId),
          ).to.equal(true);
          expect(await lookup.resolveToken(tokenHash)).to.equal(null);
          expect(await tokens.revokeRefreshByHash(rh, a.clientId)).to.equal(
            true,
          );
        });

        it('a call that names only a row id or a user needs an org to act in, and refuses without one', async () => {
          const a = await app(A);
          const rh = sha(uniq('rt'));
          const rt = await tokens.issueRefresh({
            tokenHash: rh,
            clientId: a.clientId,
            userId: U,
            orgId: A,
            scopes: ['a'],
            expiresAt: future(),
          });
          expect(await rejected(tokens.rotateRefresh(rt._id))).to.be.instanceOf(
            MissingOrgContext,
          );
          expect(await rejected(tokens.revokeAllForUser(U))).to.be.instanceOf(
            MissingOrgContext,
          );
          expect(await inOrg(A, () => tokens.rotateRefresh(rt._id))).to.equal(
            true,
          );
        });

        it('the org of a client in a context of another org: nothing is found, nothing is changed', async () => {
          const a = await app(A);
          const { tokenHash, dto } = await access(A, a.clientId);
          const code = uniq('code');
          await tokens.createCode({
            code,
            clientId: a.clientId,
            userId: U,
            orgId: A,
            redirectUri: 'https://x.example/cb',
            scopes: ['a'],
            expiresAt: future(),
          });
          await inOrg(B, async () => {
            expect(await apps.findLiveByClientId(a.clientId)).to.equal(null);
            expect(await apps.findByClientId(a.clientId)).to.equal(null);
            expect(
              await tokens.revokeAccessByHash(tokenHash, a.clientId),
            ).to.equal(false);
            expect(await tokens.consumeCode(code, a.clientId)).to.equal(false);
            expect(await tokens.findCode(code, a.clientId)).to.equal(null);
            expect(
              await tokens.listLiveAccessForUser(a.clientId, U, 10),
            ).to.deep.equal([]);
            expect(
              await tokens.revokeAccessById(dto._id, a.clientId, U, U),
            ).to.equal(false);
            await tokens.revokeAllForClient(a.clientId);
          });
          expect((await lookup.resolveToken(tokenHash))?.orgId).to.equal(A);
          expect(await tokens.consumeCode(code, a.clientId)).to.equal(true);
        });

        it('an organisation chosen by the caller for a write must be the ambient one', async () => {
          const a = await app(A);
          const e = await rejected(inOrg(B, () => access(A, a.clientId)));
          expect(e.message).to.match(/differs|cannot bind/);
        });
      });

      describe('authorization codes', () => {
        it('only the SHA-256 of a code is stored', async () => {
          const a = await app(A);
          const code = uniq('code');
          await tokens.createCode({
            code,
            clientId: a.clientId,
            userId: U,
            orgId: A,
            redirectUri: 'https://x.example/cb',
            scopes: ['a'],
            expiresAt: future(),
          });
          const rows = await stack.admin(
            async (c) =>
              (
                await c.query(
                  'SELECT code_hash FROM oauth.authorization_codes WHERE client_id = $1',
                  [a.clientId],
                )
              ).rows,
          );
          expect(rows).to.deep.equal([{ code_hash: sha(code) }]);
          expect((await tokens.findCode(code, a.clientId))?.code).to.equal(
            code,
          );
        });

        it('24 concurrent redemptions of one code through the pooler: exactly one wins', async () => {
          const a = await app(A);
          for (let round = 0; round < 5; round++) {
            const code = uniq('code');
            await tokens.createCode({
              code,
              clientId: a.clientId,
              userId: U,
              orgId: A,
              redirectUri: 'https://x.example/cb',
              scopes: ['a'],
              expiresAt: future(),
            });
            const results = await Promise.all(
              Array.from({ length: 24 }, () =>
                tokens.consumeCode(code, a.clientId),
              ),
            );
            expect(results.filter(Boolean), `round ${round}`).to.have.length(1);
          }
        });
      });

      describe('the device flow', () => {
        const dev = (over: Record<string, unknown> = {}) => ({
          deviceCodeHash: sha(uniq('dc')),
          userCode: uniq('UC')
            .replace(/[^A-Z0-9]/gi, '')
            .toUpperCase(),
          clientId: uniq('client'),
          scopes: ['a'],
          interval: 5,
          expiresAt: future(),
          ...over,
        });
        const find = async (d: ReturnType<typeof dev>) =>
          (await tokens.findDeviceByUserCode(d.userCode))!;

        it("a user code is unique, and the repository says so with the seam's error", async () => {
          const d = dev();
          await tokens.createDevice(d);
          expect(
            await rejected(tokens.createDevice(dev({ userCode: d.userCode }))),
          ).to.be.instanceOf(UniqueConstraintError);
        });

        it('an approval takes the person and the org from the unit; arguments that differ are refused', async () => {
          const d = dev();
          await tokens.createDevice(d);
          const row = await find(d);
          const user = newId();
          await rejected(
            inOrg(
              A,
              () =>
                tokens.decideDevice(row._id, {
                  status: OAuthDeviceCodeStatus.APPROVED,
                  userId: newId(),
                  orgId: A,
                }),
              user,
            ),
          );
          await rejected(
            inOrg(
              A,
              () =>
                tokens.decideDevice(row._id, {
                  status: OAuthDeviceCodeStatus.APPROVED,
                  userId: user,
                  orgId: B,
                }),
              user,
            ),
          );
          expect((await find(d)).status).to.equal('pending');
          await rejected(
            tokens.decideDevice(row._id, {
              status: OAuthDeviceCodeStatus.APPROVED,
              userId: user,
              orgId: A,
            }),
          ); // no session at all
          await inOrg(
            A,
            () =>
              tokens.decideDevice(row._id, {
                status: OAuthDeviceCodeStatus.APPROVED,
                userId: user,
                orgId: A,
              }),
            user,
          );
          expect(await find(d)).to.include({
            status: 'approved',
            userId: user,
            orgId: A,
          });
          // the poll has no session: the claim hands the approving person and org to exactly one of several callers
          const claims = await Promise.all(
            Array.from({ length: 12 }, () =>
              tokens.claimApprovedDevice(row._id),
            ),
          );
          expect(claims.filter((c) => c !== null)).to.have.length(1);
          expect(claims.find((c) => c)).to.include({ userId: user, orgId: A });
          expect(
            await tokens.findDeviceByHash(d.deviceCodeHash, d.clientId),
          ).to.equal(null);
        });

        it('a denied or expired authorization cannot be claimed', async () => {
          const denied = dev();
          const expired = dev({ expiresAt: new Date(Date.now() - 1000) });
          for (const d of [denied, expired]) await tokens.createDevice(d);
          const user = newId();
          await tokens.decideDevice((await find(denied))._id, {
            status: OAuthDeviceCodeStatus.DENIED,
          });
          const ex = await find(expired);
          await inOrg(
            A,
            () =>
              tokens.decideDevice(ex._id, {
                status: OAuthDeviceCodeStatus.APPROVED,
                userId: user,
                orgId: A,
              }),
            user,
          );
          expect(
            await tokens.claimApprovedDevice((await find(denied))._id),
          ).to.equal(null);
          expect(await tokens.claimApprovedDevice(ex._id)).to.equal(null);
        });

        it('the sweeper removes what expired an hour ago and nothing newer, in every table that expires', async () => {
          const old = dev({ expiresAt: past() });
          const recent = dev({ expiresAt: new Date(Date.now() - 60_000) });
          for (const d of [old, recent]) await tokens.createDevice(d);
          const a = await app(A);
          const oldAccess = await access(A, a.clientId, { expiresAt: past() });
          const liveAccess = await access(A, a.clientId);
          const oldCode = uniq('code');
          await tokens.createCode({
            code: oldCode,
            clientId: a.clientId,
            userId: U,
            orgId: A,
            redirectUri: 'https://x.example/cb',
            scopes: ['a'],
            expiresAt: past(),
          });
          const sweeper = new TtlSweeper({
            info: () => undefined,
            error: () => undefined,
          } as never);
          const removed = await sweeper.sweepOnce();
          expect(removed.device_authorizations).to.be.at.least(1);
          expect(removed.access_tokens).to.be.at.least(1);
          expect(removed.authorization_codes).to.be.at.least(1);
          expect(await tokens.findDeviceByUserCode(old.userCode)).to.equal(
            null,
          );
          expect(
            await tokens.findDeviceByUserCode(recent.userCode),
          ).to.not.equal(null);
          expect(await tokens.findCode(oldCode, a.clientId)).to.equal(null);
          expect(
            (await lookup.resolveToken(liveAccess.tokenHash))?._id,
          ).to.equal(liveAccess.dto._id);
          expect(oldAccess.dto._id).to.not.equal(liveAccess.dto._id);
        });
      });

      describe('apps', () => {
        it('the slug is numbered per org and goes back with a create that rolls back', async () => {
          const org = newId();
          expect((await app(org)).slug).to.equal('oauthapp-1001');
          const e = await rejected(
            inOrg(org, () =>
              new PgUnitOfWork().run(async (tx) => {
                await kernelTx(tx).query(
                  "/* pipeshub:oauth.test_slug */ INSERT INTO identity.slug_counters (org_id, kind, next_value) VALUES ($1, 'OAuthApp', 1) ON CONFLICT (org_id, kind) DO UPDATE SET next_value = identity.slug_counters.next_value + 1",
                  [org],
                );
                throw new Error('abort');
              }),
            ),
          );
          expect(e.message).to.equal('abort');
          expect((await app(org)).slug).to.equal('oauthapp-1002');
          expect((await app(newId())).slug).to.equal('oauthapp-1001');
        });

        it('two replacements of an array queue instead of overwriting each other: both are counted', async () => {
          const org = newId();
          const a = await app(org);
          const results = await Promise.all(
            ['x', 'y', 'z', 'w'].map((s) =>
              apps.updateOwned(org, U, a._id, { allowedScopes: [s] }),
            ),
          );
          expect(results.every((r) => r !== null)).to.equal(true);
          expect((await apps.findOwned(org, U, a._id))?.__v).to.equal(4);
        });

        it('org B sees none of org A, and cannot write into it', async () => {
          const a = await app(A);
          const { dto } = await access(A, a.clientId);
          await inOrg(B, async () => {
            expect(await apps.findOwned(B, U, a._id)).to.equal(null);
            expect(
              (await apps.listOwned(B, U, { skip: 0, limit: 100 })).rows.map(
                (r) => r._id,
              ),
            ).to.not.include(a._id);
            const seen = await new PgUnitOfWork().run(
              (tx) =>
                kernelTx(tx).query(
                  'SELECT count(*)::int AS n FROM oauth.access_tokens WHERE id = $1',
                  [dto._id],
                ),
              { readOnly: true },
            );
            expect(seen.rows[0]).to.deep.equal({ n: 0 });
            await rejected(
              new PgUnitOfWork().run((tx) =>
                kernelTx(tx).query(
                  `INSERT INTO oauth.access_tokens (org_id, id, token_hash, client_id, scopes, expires_at, created_at, updated_at) VALUES ($1, $2, 'h', 'c', '{a}', now(), now(), now())`,
                  [A, newId()],
                ),
              ),
            );
          });
        });
      });
    },
  );
}
