/**
 * Contract every OAuthTokenRepository and IdentityLookup must meet: token
 * issue, refresh rotation, revocation, personal-token lists, authorization
 * codes, the device flow, and the lookups made before an org is known.
 * Store-neutral: the store under test supplies the objects and raw helpers.
 */
import { expect } from 'chai';
import { createHash } from 'crypto';
import { UniqueConstraintError } from '../../../src/libs/repositories/errors';
import type { IdentityLookup } from '../../../src/libs/repositories/identity-lookup';
import {
  OAuthAppStatus,
  OAuthGrantType,
  type OAuthAppRepository,
} from '../../../src/libs/repositories/oauth-app';
import {
  OAuthDeviceCodeStatus,
  type NewAccessToken,
  type NewDeviceCode,
  type OAuthTokenRepository,
} from '../../../src/libs/repositories/oauth-token';

export interface OAuthTokenFixture {
  tokens: OAuthTokenRepository;
  lookup: IdentityLookup;
  apps: OAuthAppRepository;
  newId(): string;
  /** Key names of a stored access token row. */
  rawAccessKeys(id: string): Promise<string[]>;
  rawAccess(id: string): Promise<Record<string, unknown> | null>;
  rawRefresh(id: string): Promise<Record<string, unknown> | null>;
  rawDevice(id: string): Promise<Record<string, unknown> | null>;
  setAccessCreatedAt(id: string, at: Date): Promise<void>;
}

const sha = (v: string): string => createHash('sha256').update(v).digest('hex');
const future = (ms = 3600_000): Date => new Date(Date.now() + ms);
const past = (): Date => new Date(Date.now() - 60_000);
/** A key that is an operator object must never match a row: the store answers not found or refuses. */
const neverMatches = async (p: Promise<unknown>): Promise<void> => {
  try {
    expect(await p).to.equal(null);
  } catch (e) {
    expect((e as Error).name).to.not.equal('AssertionError');
  }
};

export function describeOAuthTokenContract(fx: OAuthTokenFixture): void {
  const { tokens, lookup } = fx;
  const HEX = /^[0-9a-f]{24}$/;
  // one organisation for the fixtures: a user, client or code belongs to one organisation, and the calls that name only those act in it
  const ORG = fx.newId();
  let n = 0;
  const uniq = (p: string): string => `${p}-${process.pid}-${Date.now()}-${n++}`;
  const access = (over: Partial<NewAccessToken> = {}): NewAccessToken => ({
    tokenHash: sha(uniq('at')),
    clientId: uniq('client'),
    userId: fx.newId(),
    orgId: ORG,
    scopes: ['org:read'],
    expiresAt: future(),
    ...over,
  });
  const device = (over: Partial<NewDeviceCode> = {}): NewDeviceCode => ({
    deviceCodeHash: sha(uniq('dev')),
    userCode: uniq('UC').replace(/[^A-Z0-9]/gi, '').toUpperCase(),
    clientId: uniq('client'),
    scopes: ['org:read'],
    interval: 5,
    expiresAt: future(),
    ...over,
  });

  describe('issue', () => {
    it('issueAccess returns a DTO with string ids and the stored row\'s keys; only the hash is kept', async () => {
      const input = access({ name: 'ci', actorUserId: fx.newId() });
      const dto = await tokens.issueAccess(input);
      expect(dto._id).to.match(HEX);
      expect(dto).to.include({ tokenHash: input.tokenHash, clientId: input.clientId, userId: input.userId, orgId: input.orgId, name: 'ci', actorUserId: input.actorUserId, isRevoked: false });
      expect(dto.scopes).to.deep.equal(['org:read']);
      expect(dto.expiresAt).to.be.instanceOf(Date);
      expect(Object.keys(dto).sort()).to.deep.equal((await fx.rawAccessKeys(dto._id)).sort());
    });

    it('a knowledge limit rides on the access token, the code and the device authorization; none stays absent', async () => {
      const limit = { apps: ['a-1'], recordGroups: ['g-1'], records: [] };
      const limited = await tokens.issueAccess(access({ knowledgeLimit: limit }));
      expect(limited.knowledgeLimit).to.deep.equal(limit);
      expect(await tokens.issueAccess(access())).to.not.have.property('knowledgeLimit');
      const live = await tokens.listLiveAccessForUser(limited.clientId, limited.userId!, 10);
      expect(live[0]?.knowledgeLimit).to.deep.equal(limit);

      const c = {
        code: uniq('code'), clientId: uniq('client'), userId: fx.newId(), orgId: ORG,
        redirectUri: 'https://a.example/cb', scopes: ['org:read'], expiresAt: future(), knowledgeLimit: limit,
      };
      await tokens.createCode(c);
      expect((await tokens.findCode(c.code, c.clientId))?.knowledgeLimit).to.deep.equal(limit);

      const d = device({ knowledgeLimit: limit });
      await tokens.createDevice(d);
      expect((await tokens.findDeviceByUserCode(d.userCode))?.knowledgeLimit).to.deep.equal(limit);
      expect((await tokens.findDeviceByHash(d.deviceCodeHash, d.clientId))?.knowledgeLimit).to.deep.equal(limit);
      const row = await tokens.findDeviceByUserCode(d.userCode);
      await tokens.decideDevice(row!._id, { status: OAuthDeviceCodeStatus.APPROVED, userId: fx.newId(), orgId: ORG });
      expect((await tokens.claimApprovedDevice(row!._id))?.knowledgeLimit).to.deep.equal(limit);
      const plain = device();
      await tokens.createDevice(plain);
      expect(await tokens.findDeviceByUserCode(plain.userCode)).to.not.have.property('knowledgeLimit');
    });

    it('a client-credentials access token has no user', async () => {
      const dto = await tokens.issueAccess(access({ userId: undefined }));
      expect(dto).to.not.have.property('userId');
    });

    it('issueRefresh returns a DTO with rotationCount 0', async () => {
      const userId = fx.newId();
      const orgId = fx.newId();
      const dto = await tokens.issueRefresh({ tokenHash: sha(uniq('rt')), clientId: 'c', userId, orgId, scopes: ['a'], expiresAt: future() });
      expect(dto).to.include({ userId, orgId, rotationCount: 0, isRevoked: false });
      expect(dto._id).to.match(HEX);
    });

    it('two tokens never share a hash', async () => {
      const input = access();
      await tokens.issueAccess(input);
      let caught: unknown;
      try {
        await tokens.issueAccess({ ...input });
      } catch (e) {
        caught = e;
      }
      expect(caught).to.be.instanceOf(UniqueConstraintError);
    });
  });

  describe('lookups before the org is known (IdentityLookup)', () => {
    it('resolveToken returns the live token\'s org, user, client, scopes, expiry and recency, nothing else', async () => {
      const input = access();
      const dto = await tokens.issueAccess(input);
      const found = await lookup.resolveToken(input.tokenHash);
      expect(found).to.deep.equal({
        _id: dto._id,
        orgId: input.orgId,
        userId: input.userId,
        clientId: input.clientId,
        scopes: input.scopes,
        expiresAt: input.expiresAt,
        lastUsedAt: undefined,
      });
      expect(JSON.parse(JSON.stringify(found))).to.have.all.keys('_id', 'orgId', 'userId', 'clientId', 'scopes', 'expiresAt');
    });

    it('resolveToken does not find an unknown or revoked token, and takes the hash as a value', async () => {
      const input = access();
      await tokens.issueAccess(input);
      expect(await lookup.resolveToken(sha('unknown'))).to.equal(null);
      await neverMatches(lookup.resolveToken({ $ne: 'x' } as unknown as string));
      expect(await tokens.revokeAccessByHash(input.tokenHash, input.clientId)).to.equal(true);
      expect(await lookup.resolveToken(input.tokenHash)).to.equal(null);
    });

    it('resolveToken shows the recency stamp once the token was used', async () => {
      const input = access();
      const dto = await tokens.issueAccess(input);
      const at = new Date(Date.now() - 1000);
      await tokens.touchAccessLastUsed(dto._id, at);
      expect((await lookup.resolveToken(input.tokenHash))?.lastUsedAt?.getTime()).to.equal(at.getTime());
    });

    it('resolveRefresh finds live refresh tokens only', async () => {
      const userId = fx.newId();
      const orgId = fx.newId();
      const h = sha(uniq('rt'));
      const rt = await tokens.issueRefresh({ tokenHash: h, clientId: 'c', userId, orgId, scopes: ['a'], expiresAt: future() });
      expect(await lookup.resolveRefresh(h)).to.deep.equal({ _id: rt._id, orgId, userId, clientId: 'c', scopes: ['a'], expiresAt: rt.expiresAt, rotationCount: 0 });
      expect(await tokens.rotateRefresh(rt._id)).to.equal(true);
      expect(await lookup.resolveRefresh(h)).to.equal(null);
      expect(await lookup.resolveRefresh(sha('nope'))).to.equal(null);
    });

    it('resolveClient answers the org, app and status of a live client, even if suspended; none for deleted or unknown', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const clientId = uniq('rc');
      const app = await fx.apps.create({ clientId, clientSecretEncrypted: 'e', name: 'n', orgId: org, createdBy: me, redirectUris: [], allowedGrantTypes: [OAuthGrantType.REFRESH_TOKEN], allowedScopes: ['a'], isConfidential: true });
      expect(await lookup.resolveClient(clientId)).to.deep.equal({ orgId: org, appId: app._id, status: OAuthAppStatus.ACTIVE });
      await fx.apps.updateOwned(org, me, app._id, { status: OAuthAppStatus.SUSPENDED });
      expect((await lookup.resolveClient(clientId))?.status).to.equal(OAuthAppStatus.SUSPENDED);
      expect(await lookup.resolveClient('unknown-client')).to.equal(null);
      await fx.apps.updateOwned(org, me, app._id, { isDeleted: true });
      expect(await lookup.resolveClient(clientId)).to.equal(null);
    });
  });

  describe('refresh rotation', () => {
    it('findRefreshByHash returns the row whether or not it is revoked; rotateRefresh marks it', async () => {
      const h = sha(uniq('rt'));
      const rt = await tokens.issueRefresh({ tokenHash: h, clientId: 'c', userId: fx.newId(), orgId: ORG, scopes: ['a'], expiresAt: future() });
      expect((await tokens.findRefreshByHash(h))?._id).to.equal(rt._id);
      expect(await tokens.rotateRefresh(rt._id)).to.equal(true);
      const again = await tokens.findRefreshByHash(h);
      expect(again?.isRevoked).to.equal(true);
      expect(again?.revokedAt).to.be.instanceOf(Date);
      expect(await tokens.findRefreshByHash(sha('nope'))).to.equal(null);
    });
  });

  describe('single use (races)', () => {
    const ROUNDS = 40;
    const CONTENDERS = 4;

    it(`rotateRefresh: exactly one of ${CONTENDERS} concurrent rotations wins, ${ROUNDS} rounds`, async () => {
      for (let i = 0; i < ROUNDS; i++) {
        const rt = await tokens.issueRefresh({ tokenHash: sha(uniq('rt')), clientId: 'c', userId: fx.newId(), orgId: ORG, scopes: ['a'], expiresAt: future() });
        const results = await Promise.all(Array.from({ length: CONTENDERS }, () => tokens.rotateRefresh(rt._id)));
        expect(results.filter(Boolean), `round ${i}`).to.have.length(1);
        expect(await tokens.rotateRefresh(rt._id)).to.equal(false);
      }
    });

    it('rotateRefresh refuses a refresh token that is already revoked', async () => {
      const h = sha(uniq('rt'));
      const rt = await tokens.issueRefresh({ tokenHash: h, clientId: 'c', userId: fx.newId(), orgId: ORG, scopes: ['a'], expiresAt: future() });
      await tokens.revokeRefreshByHash(h, 'c');
      expect(await tokens.rotateRefresh(rt._id)).to.equal(false);
    });

    it(`consumeCode: exactly one of ${CONTENDERS} concurrent redemptions wins, ${ROUNDS} rounds`, async () => {
      for (let i = 0; i < ROUNDS; i++) {
        const c = { code: uniq('code'), clientId: uniq('client'), userId: fx.newId(), orgId: ORG, redirectUri: 'https://a.example/cb', scopes: ['a'], expiresAt: future() };
        await tokens.createCode(c);
        const results = await Promise.all(Array.from({ length: CONTENDERS }, () => tokens.consumeCode(c.code, c.clientId)));
        expect(results.filter(Boolean), `round ${i}`).to.have.length(1);
        expect((await tokens.findCode(c.code, c.clientId))?.isUsed).to.equal(true);
      }
    });

    it('consumeCode refuses an expired code, an unknown code and another client\'s code, and stamps usedAt on success', async () => {
      const base = { userId: fx.newId(), orgId: ORG, redirectUri: 'https://a.example/cb', scopes: ['a'] };
      const live = { ...base, code: uniq('code'), clientId: uniq('client'), expiresAt: future() };
      const old = { ...base, code: uniq('code'), clientId: uniq('client'), expiresAt: past() };
      await tokens.createCode(live);
      await tokens.createCode(old);
      expect(await tokens.consumeCode(live.code, 'another-client')).to.equal(false);
      expect(await tokens.consumeCode(old.code, old.clientId)).to.equal(false);
      expect(await tokens.consumeCode('unknown', live.clientId)).to.equal(false);
      expect(await tokens.consumeCode(live.code, live.clientId)).to.equal(true);
      expect(await tokens.consumeCode(live.code, live.clientId)).to.equal(false);
      expect((await tokens.findCode(live.code, live.clientId))?.usedAt).to.be.instanceOf(Date);
    });
  });

  describe('revocation', () => {
    it('revokeAccessByHash needs the issuing client and revokes once', async () => {
      const input = access();
      await tokens.issueAccess(input);
      expect(await tokens.revokeAccessByHash(input.tokenHash, 'someone-else')).to.equal(false);
      expect(await tokens.revokeAccessByHash(input.tokenHash, input.clientId)).to.equal(true);
      expect(await tokens.revokeAccessByHash(input.tokenHash, input.clientId)).to.equal(false);
    });

    it('revokeRefreshByHash needs the issuing client and revokes once', async () => {
      const h = sha(uniq('rt'));
      await tokens.issueRefresh({ tokenHash: h, clientId: 'cx', userId: fx.newId(), orgId: ORG, scopes: ['a'], expiresAt: future() });
      expect(await tokens.revokeRefreshByHash(h, 'other')).to.equal(false);
      expect(await tokens.revokeRefreshByHash(h, 'cx')).to.equal(true);
      expect(await tokens.revokeRefreshByHash(h, 'cx')).to.equal(false);
    });

    it('revokeIssued revokes a fresh access token and refresh token by hash', async () => {
      const a = access();
      await tokens.issueAccess(a);
      const rh = sha(uniq('rt'));
      await tokens.issueRefresh({ tokenHash: rh, clientId: a.clientId, userId: a.userId!, orgId: a.orgId, scopes: ['a'], expiresAt: future() });
      await tokens.revokeIssued(a.tokenHash, rh);
      expect(await lookup.resolveToken(a.tokenHash)).to.equal(null);
      expect(await lookup.resolveRefresh(rh)).to.equal(null);
      const solo = access();
      await tokens.issueAccess(solo);
      await tokens.revokeIssued(solo.tokenHash);
      expect(await lookup.resolveToken(solo.tokenHash)).to.equal(null);
    });

    it('revokeAllForClient revokes both kinds for that client only', async () => {
      const client = uniq('app');
      const a = access({ clientId: client });
      const other = access();
      await tokens.issueAccess(a);
      await tokens.issueAccess(other);
      const rh = sha(uniq('rt'));
      await tokens.issueRefresh({ tokenHash: rh, clientId: client, userId: fx.newId(), orgId: a.orgId, scopes: ['a'], expiresAt: future() });
      await tokens.revokeAllForClient(client);
      expect(await lookup.resolveToken(a.tokenHash)).to.equal(null);
      expect(await lookup.resolveRefresh(rh)).to.equal(null);
      expect(await lookup.resolveToken(other.tokenHash)).to.not.equal(null);
    });

    it('revokeAllForUser revokes the user\'s tokens under every client, nobody else\'s', async () => {
      const user = fx.newId();
      const a = access({ userId: user });
      const b = access({ userId: user });
      const other = access();
      for (const t of [a, b, other]) await tokens.issueAccess(t);
      const rh = sha(uniq('rt'));
      await tokens.issueRefresh({ tokenHash: rh, clientId: 'z', userId: user, orgId: a.orgId, scopes: ['a'], expiresAt: future() });
      await tokens.revokeAllForUser(user);
      expect(await lookup.resolveToken(a.tokenHash)).to.equal(null);
      expect(await lookup.resolveToken(b.tokenHash)).to.equal(null);
      expect(await lookup.resolveRefresh(rh)).to.equal(null);
      expect(await lookup.resolveToken(other.tokenHash)).to.not.equal(null);
    });

    it('revokeAllForClientUser and the reuse variant only touch that client and user; the reason is recorded', async () => {
      const user = fx.newId();
      const client = uniq('app');
      const mine = access({ userId: user, clientId: client });
      const otherClient = access({ userId: user });
      const otherUser = access({ clientId: client });
      for (const t of [mine, otherClient, otherUser]) await tokens.issueAccess(t);
      await tokens.revokeAllForClientUser(client, user);
      expect(await lookup.resolveToken(mine.tokenHash)).to.equal(null);
      expect(await lookup.resolveToken(otherClient.tokenHash)).to.not.equal(null);
      expect(await lookup.resolveToken(otherUser.tokenHash)).to.not.equal(null);

      const reused = await tokens.issueAccess(access({ userId: user, clientId: client }));
      await tokens.revokeAllForClientUserBecause(client, user, 'code_reuse_detected');
      expect((await fx.rawAccess(reused._id))?.revokedReason).to.equal('code_reuse_detected');
      expect(await lookup.resolveToken(otherUser.tokenHash)).to.not.equal(null);
    });

    it('revokeDelegatedForActor revokes live delegated tokens and reports how many; records who and why', async () => {
      const actor = fx.newId();
      const by = fx.newId();
      const d1 = await tokens.issueAccess(access({ actorUserId: actor }));
      const d2 = await tokens.issueAccess(access({ actorUserId: actor }));
      const plain = access();
      await tokens.issueAccess(plain);
      expect(await tokens.revokeDelegatedForActor(actor, by, 'switched off')).to.equal(2);
      expect(await tokens.revokeDelegatedForActor(actor, by, 'switched off')).to.equal(0);
      const row = await fx.rawAccess(d1._id);
      expect(row).to.include({ isRevoked: true, revokedReason: 'switched off' });
      expect(String(row?.revokedBy)).to.equal(by);
      expect((await fx.rawAccess(d2._id))?.isRevoked).to.equal(true);
      expect(await lookup.resolveToken(plain.tokenHash)).to.not.equal(null);
      const noWho = await tokens.issueAccess(access({ actorUserId: actor }));
      await tokens.revokeDelegatedForActor(actor, undefined, 'r');
      expect(await fx.rawAccess(noWho._id)).to.not.have.property('revokedBy');
    });

    it('revokeAccessById is scoped to client and user, takes a bad id as not found, and records who revoked', async () => {
      const user = fx.newId();
      const admin = fx.newId();
      const client = uniq('pat');
      const t = await tokens.issueAccess(access({ userId: user, clientId: client }));
      expect(await tokens.revokeAccessById('not-an-id', client, user, user)).to.equal(false);
      expect(await tokens.revokeAccessById(t._id, client, fx.newId(), user)).to.equal(false);
      expect(await tokens.revokeAccessById(t._id, 'other-client', user, user)).to.equal(false);
      expect(await tokens.revokeAccessById(t._id, client, user, admin, 'because')).to.equal(true);
      expect(await tokens.revokeAccessById(t._id, client, user, admin)).to.equal(false);
      const row = await fx.rawAccess(t._id);
      expect(row).to.include({ isRevoked: true, revokedReason: 'because' });
      expect(String(row?.revokedBy)).to.equal(admin);
    });

    it('revokeAccessByIdForClient revokes any user\'s token of that client (administrator)', async () => {
      const client = uniq('pat');
      const admin = fx.newId();
      const t = await tokens.issueAccess(access({ clientId: client }));
      expect(await tokens.revokeAccessByIdForClient('bad', client, admin)).to.equal(false);
      expect(await tokens.revokeAccessByIdForClient(t._id, 'other', admin)).to.equal(false);
      expect(await tokens.revokeAccessByIdForClient(t._id, client, admin, 'left the company')).to.equal(true);
      expect(await tokens.revokeAccessByIdForClient(t._id, client, admin)).to.equal(false);
    });
  });

  describe('lists', () => {
    it('only live tokens are listed, newest first, honouring the limit', async () => {
      const client = uniq('lst');
      const user = fx.newId();
      const base = Date.now();
      const mine: string[] = [];
      for (let i = 0; i < 4; i++) {
        const t = await tokens.issueAccess(access({ clientId: client, userId: user }));
        await fx.setAccessCreatedAt(t._id, new Date(base - (4 - i) * 1000));
        mine.push(t._id);
      }
      const expired = await tokens.issueAccess(access({ clientId: client, userId: user, expiresAt: past() }));
      const revoked = access({ clientId: client, userId: user });
      await tokens.issueAccess(revoked);
      await tokens.revokeAccessByHash(revoked.tokenHash, client);
      await tokens.issueAccess(access({ clientId: client }));
      const list = await tokens.listLiveAccessForUser(client, user, 100);
      expect(list.map((t) => t._id)).to.deep.equal([...mine].reverse());
      expect(list.map((t) => t._id)).to.not.include(expired._id);
      expect((await tokens.listLiveAccessForUser(client, user, 2)).map((t) => t._id)).to.deep.equal([mine[3], mine[2]]);
      expect(await tokens.countLiveAccessForUser(client, user)).to.equal(4);
    });

    it('listLiveForClient returns both kinds, limited per kind', async () => {
      const client = uniq('lstc');
      for (let i = 0; i < 3; i++) {
        await tokens.issueAccess(access({ clientId: client }));
        await tokens.issueRefresh({ tokenHash: sha(uniq('rt')), clientId: client, userId: fx.newId(), orgId: ORG, scopes: ['a'], expiresAt: future() });
      }
      await tokens.issueRefresh({ tokenHash: sha(uniq('rt')), clientId: client, userId: fx.newId(), orgId: ORG, scopes: ['a'], expiresAt: past() });
      const all = await tokens.listLiveForClient(client, 100);
      expect(all.access).to.have.length(3);
      expect(all.refresh).to.have.length(3);
      const capped = await tokens.listLiveForClient(client, 2);
      expect(capped.access).to.have.length(2);
      expect(capped.refresh).to.have.length(2);
    });

    it('listLiveAccessForClientPage pages through the client\'s live tokens with a total', async () => {
      const client = uniq('pg');
      const base = Date.now();
      const ids: string[] = [];
      for (let i = 0; i < 5; i++) {
        const t = await tokens.issueAccess(access({ clientId: client }));
        await fx.setAccessCreatedAt(t._id, new Date(base - (5 - i) * 1000));
        ids.push(t._id);
      }
      await tokens.issueAccess(access({ clientId: client, expiresAt: past() }));
      const newest = [...ids].reverse();
      const p1 = await tokens.listLiveAccessForClientPage(client, 1, 2);
      const p3 = await tokens.listLiveAccessForClientPage(client, 3, 2);
      expect(p1.total).to.equal(5);
      expect(p1.rows.map((r) => r._id)).to.deep.equal(newest.slice(0, 2));
      expect(p3.rows.map((r) => r._id)).to.deep.equal(newest.slice(4));
    });
  });

  describe('authorization codes', () => {
    const code = (over: Record<string, unknown> = {}) => ({
      code: uniq('code'),
      clientId: uniq('client'),
      userId: fx.newId(),
      orgId: ORG,
      redirectUri: 'https://a.example/cb',
      scopes: ['org:read'],
      expiresAt: future(),
      ...over,
    });

    it('findCode returns the code issued to that client, with PKCE material and string ids', async () => {
      const c = code({ codeChallenge: 'chal', codeChallengeMethod: 'S256' as const });
      await tokens.createCode(c);
      const found = await tokens.findCode(c.code, c.clientId);
      expect(found).to.include({ code: c.code, clientId: c.clientId, userId: c.userId, orgId: c.orgId, redirectUri: c.redirectUri, codeChallenge: 'chal', codeChallengeMethod: 'S256', isUsed: false });
      expect(found?._id).to.match(HEX);
      expect(await tokens.findCode(c.code, 'another-client')).to.equal(null);
      await neverMatches(tokens.findCode({ $ne: 'x' } as unknown as string, c.clientId));
    });

    it('consumeCode marks it used once and stamps the time; a used code is still found (reuse detection)', async () => {
      const c = code();
      await tokens.createCode(c);
      expect(await tokens.consumeCode(c.code, c.clientId)).to.equal(true);
      const found = await tokens.findCode(c.code, c.clientId);
      expect(found?.isUsed).to.equal(true);
      expect(found?.usedAt).to.be.instanceOf(Date);
    });

    it('flagCodeReplayed stamps replayedAt on that code only', async () => {
      const a = code();
      const b = code();
      await tokens.createCode(a);
      await tokens.createCode(b);
      expect((await tokens.findCode(a.code, a.clientId))).to.not.have.property('replayedAt');
      await tokens.flagCodeReplayed(a.code, a.clientId);
      await tokens.flagCodeReplayed(b.code, 'another-client');
      expect((await tokens.findCode(a.code, a.clientId))?.replayedAt).to.be.instanceOf(Date);
      expect(await tokens.findCode(b.code, b.clientId)).to.not.have.property('replayedAt');
    });

    it('deleteCode removes one code', async () => {
      const c = code();
      await tokens.createCode(c);
      const found = await tokens.findCode(c.code, c.clientId);
      await tokens.deleteCode(found!._id);
      expect(await tokens.findCode(c.code, c.clientId)).to.equal(null);
    });

    it('deleteExpiredCodes removes expired and used codes and keeps live ones', async () => {
      const expired = code({ expiresAt: past() });
      const used = code();
      const live = code();
      for (const c of [expired, used, live]) await tokens.createCode(c);
      expect(await tokens.consumeCode(used.code, used.clientId)).to.equal(true);
      expect(await tokens.deleteExpiredCodes()).to.be.at.least(2);
      expect(await tokens.findCode(expired.code, expired.clientId)).to.equal(null);
      expect(await tokens.findCode(used.code, used.clientId)).to.equal(null);
      expect(await tokens.findCode(live.code, live.clientId)).to.not.equal(null);
    });

    it('deleteUnusedCodesForUser and ForClient delete unused codes only', async () => {
      const user = fx.newId();
      const client = uniq('app');
      const a = code({ userId: user });
      const b = code({ userId: user });
      const c = code({ clientId: client });
      const used = code({ clientId: client });
      for (const x of [a, b, c, used]) await tokens.createCode(x);
      expect(await tokens.consumeCode(used.code, used.clientId)).to.equal(true);
      await tokens.deleteUnusedCodesForUser(user);
      expect(await tokens.findCode(a.code, a.clientId)).to.equal(null);
      expect(await tokens.findCode(b.code, b.clientId)).to.equal(null);
      expect(await tokens.findCode(c.code, c.clientId)).to.not.equal(null);
      await tokens.deleteUnusedCodesForClient(client);
      expect(await tokens.findCode(c.code, c.clientId)).to.equal(null);
      expect(await tokens.findCode(used.code, used.clientId)).to.not.equal(null);
    });
  });

  describe('device authorizations', () => {
    it('createDevice stores a pending authorization with no user or org', async () => {
      const d = device();
      await tokens.createDevice(d);
      const found = await tokens.findDeviceByUserCode(d.userCode);
      expect(found).to.include({ userCode: d.userCode, clientId: d.clientId, status: OAuthDeviceCodeStatus.PENDING, interval: 5 });
      expect(found).to.not.have.property('userId');
      expect(found).to.not.have.property('orgId');
      expect(found?._id).to.match(HEX);
    });

    it('a user code is unique', async () => {
      const d = device();
      await tokens.createDevice(d);
      let caught: unknown;
      try {
        await tokens.createDevice(device({ userCode: d.userCode }));
      } catch (e) {
        caught = e;
      }
      expect(caught).to.be.instanceOf(UniqueConstraintError);
    });

    it('findDeviceByHash needs the issuing client; unknown codes are not found', async () => {
      const d = device();
      await tokens.createDevice(d);
      expect((await tokens.findDeviceByHash(d.deviceCodeHash, d.clientId))?.userCode).to.equal(d.userCode);
      expect(await tokens.findDeviceByHash(d.deviceCodeHash, 'other')).to.equal(null);
      expect(await tokens.findDeviceByUserCode('NOSUCHCODE')).to.equal(null);
    });

    it('decideDevice approves for a user in an org, or denies without setting either', async () => {
      const user = fx.newId();
      const org = ORG; // the install's organisation: a device belongs to the organisation it was started in
      const yes = device();
      const no = device();
      await tokens.createDevice(yes);
      await tokens.createDevice(no);
      const y = await tokens.findDeviceByUserCode(yes.userCode);
      const nn = await tokens.findDeviceByUserCode(no.userCode);
      await tokens.decideDevice(y!._id, { status: OAuthDeviceCodeStatus.APPROVED, userId: user, orgId: org });
      await tokens.decideDevice(nn!._id, { status: OAuthDeviceCodeStatus.DENIED });
      expect(await tokens.findDeviceByUserCode(yes.userCode)).to.include({ status: OAuthDeviceCodeStatus.APPROVED, userId: user, orgId: org });
      const denied = await tokens.findDeviceByUserCode(no.userCode);
      expect(denied?.status).to.equal(OAuthDeviceCodeStatus.DENIED);
      expect(denied).to.not.have.property('userId');
    });

    it('recordDevicePoll stamps the poll time', async () => {
      const d = device();
      await tokens.createDevice(d);
      const row = await tokens.findDeviceByUserCode(d.userCode);
      expect(row).to.not.have.property('lastPolledAt');
      const at = new Date(Date.now() - 500);
      await tokens.recordDevicePoll(row!._id, at);
      expect((await tokens.findDeviceByHash(d.deviceCodeHash, d.clientId))?.lastPolledAt?.getTime()).to.equal(at.getTime());
    });

    it('claimApprovedDevice hands an approved authorization to exactly one of several concurrent polls', async () => {
      const d = device();
      await tokens.createDevice(d);
      const row = await tokens.findDeviceByUserCode(d.userCode);
      await tokens.decideDevice(row!._id, { status: OAuthDeviceCodeStatus.APPROVED, userId: fx.newId(), orgId: ORG });
      const claims = await Promise.all(Array.from({ length: 6 }, () => tokens.claimApprovedDevice(row!._id)));
      expect(claims.filter((c) => c !== null)).to.have.length(1);
      expect(await tokens.findDeviceByUserCode(d.userCode)).to.equal(null);
    });

    it('claimApprovedDevice refuses a pending, a denied and an expired authorization', async () => {
      const pending = device();
      const denied = device();
      const expired = device({ expiresAt: past() });
      for (const d of [pending, denied, expired]) await tokens.createDevice(d);
      const p = await tokens.findDeviceByUserCode(pending.userCode);
      const dn = await tokens.findDeviceByUserCode(denied.userCode);
      const ex = await tokens.findDeviceByUserCode(expired.userCode);
      await tokens.decideDevice(dn!._id, { status: OAuthDeviceCodeStatus.DENIED });
      await tokens.decideDevice(ex!._id, { status: OAuthDeviceCodeStatus.APPROVED, userId: fx.newId(), orgId: ORG });
      expect(await tokens.claimApprovedDevice(p!._id)).to.equal(null);
      expect(await tokens.claimApprovedDevice(dn!._id)).to.equal(null);
      expect(await tokens.claimApprovedDevice(ex!._id)).to.equal(null);
    });
  });
}
