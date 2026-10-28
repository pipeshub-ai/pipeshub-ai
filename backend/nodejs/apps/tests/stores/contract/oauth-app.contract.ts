/**
 * Contract every OAuthAppRepository must meet. Store-neutral: the store under
 * test supplies the repository and a few raw-store helpers.
 */
import { expect } from 'chai';
import { UniqueConstraintError } from '../../../src/libs/repositories/errors';
import {
  OAuthAppStatus,
  OAuthGrantType,
  type NewOAuthApp,
  type OAuthAppRepository,
} from '../../../src/libs/repositories/oauth-app';

export interface OAuthAppFixture {
  repo: OAuthAppRepository;
  newId(): string;
  /** Key names of the stored row (the shape the services used to read). */
  rawKeys(id: string): Promise<string[]>;
  /** Makes a row older, so ordering does not depend on the clock. */
  setCreatedAt(id: string, at: Date): Promise<void>;
}

export function describeOAuthAppContract(fx: OAuthAppFixture): void {
  const { repo } = fx;
  const HEX = /^[0-9a-f]{24}$/;
  const mk = (
    org: string,
    user: string,
    over: Partial<NewOAuthApp> = {},
  ): NewOAuthApp => ({
    clientId: `cid-${fx.newId()}`,
    clientSecretEncrypted: 'enc',
    name: 'App',
    orgId: org,
    createdBy: user,
    redirectUris: ['https://a.example/cb'],
    allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
    allowedScopes: ['org:read'],
    isConfidential: true,
    ...over,
  });

  it('create returns a DTO with string ids, defaults, a slug, and the same keys as the stored row', async () => {
    const org = fx.newId();
    const user = fx.newId();
    const dto = await repo.create(mk(org, user, { description: 'd' }));
    expect(dto._id).to.match(HEX);
    expect(dto.orgId).to.equal(org);
    expect(dto.createdBy).to.equal(user);
    expect(dto.status).to.equal(OAuthAppStatus.ACTIVE);
    expect(dto).to.include({
      isDeleted: false,
      accessTokenLifetime: 3600,
      refreshTokenLifetime: 2592000,
      isConfidential: true,
    });
    expect(dto.slug).to.be.a('string').and.not.equal('');
    expect(dto.createdAt).to.be.instanceOf(Date);
    expect(dto.__v).to.equal(0);
    expect(Object.keys(dto).sort()).to.deep.equal((await fx.rawKeys(dto._id)).sort());
    expect(JSON.parse(JSON.stringify(dto)).orgId).to.equal(org);
  });

  it('create rejects a second app with the same client id', async () => {
    const org = fx.newId();
    const input = mk(org, fx.newId());
    await repo.create(input);
    let caught: unknown;
    try {
      await repo.create({ ...input, name: 'Other' });
    } catch (e) {
      caught = e;
    }
    expect(caught).to.be.instanceOf(UniqueConstraintError);
  });

  it('racing creates of one client id leave exactly one app', async () => {
    const input = mk(fx.newId(), fx.newId());
    const results = await Promise.allSettled(
      Array.from({ length: 6 }, () => repo.create({ ...input })),
    );
    expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1);
    for (const r of results) {
      if (r.status === 'rejected') expect(r.reason).to.be.instanceOf(UniqueConstraintError);
    }
    expect((await repo.findByClientId(input.clientId))?.clientId).to.equal(input.clientId);
  });

  describe('owned apps', () => {
    it('findOwned returns the creator\'s app in the org only', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const app = await repo.create(mk(org, me));
      expect((await repo.findOwned(org, me, app._id))?._id).to.equal(app._id);
      expect(await repo.findOwned(org, fx.newId(), app._id)).to.equal(null);
      expect(await repo.findOwned(fx.newId(), me, app._id)).to.equal(null);
      expect(await repo.findOwned(org, me, fx.newId())).to.equal(null);
    });

    it('internal and dynamic clients and deleted apps are not owned apps', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const internal = [
        await repo.create(mk(org, me, { clientId: `pat-system:${org}` })),
        await repo.create(mk(org, me, { clientId: `svc-system:${org}` })),
        await repo.create(mk(org, me, { clientId: 'pipeshub-agent' })),
        await repo.create(mk(org, me, { isDynamic: true })),
      ];
      const gone = await repo.create(mk(org, me));
      await repo.updateOwned(org, me, gone._id, { isDeleted: true, deletedBy: me, status: OAuthAppStatus.REVOKED });
      for (const a of [...internal, gone]) {
        expect(await repo.findOwned(org, me, a._id), a.clientId).to.equal(null);
      }
      expect((await repo.listOwned(org, me, { skip: 0, limit: 10 })).total).to.equal(0);
    });

    it('listOwned: newest first, skip and limit, total, status filter, search', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const now = Date.now();
      const names = ['Alpha tool', 'Beta (v2) tool', 'Gamma'];
      const made = [];
      for (const [i, name] of names.entries()) {
        const a = await repo.create(mk(org, me, { name, description: i === 2 ? 'find me' : undefined }));
        await fx.setCreatedAt(a._id, new Date(now - (names.length - i) * 1000));
        made.push(a);
      }
      await repo.create(mk(org, fx.newId(), { name: 'Not mine' }));
      await repo.updateOwned(org, me, made[1]._id, { status: OAuthAppStatus.SUSPENDED });

      const all = await repo.listOwned(org, me, { skip: 0, limit: 10 });
      expect(all.total).to.equal(3);
      expect(all.rows.map((r) => r.name)).to.deep.equal(['Gamma', 'Beta (v2) tool', 'Alpha tool']);
      const page = await repo.listOwned(org, me, { skip: 1, limit: 1 });
      expect(page.total).to.equal(3);
      expect(page.rows.map((r) => r.name)).to.deep.equal(['Beta (v2) tool']);
      expect((await repo.listOwned(org, me, { skip: 0, limit: 10, status: 'suspended' })).rows.map((r) => r.name)).to.deep.equal(['Beta (v2) tool']);
      expect((await repo.listOwned(org, me, { skip: 0, limit: 10, search: 'ALPHA' })).total).to.equal(1);
      expect((await repo.listOwned(org, me, { skip: 0, limit: 10, search: 'find' })).rows.map((r) => r.name)).to.deep.equal(['Gamma']);
      // regex metacharacters are literals, never a pattern
      expect((await repo.listOwned(org, me, { skip: 0, limit: 10, search: '(v2)' })).total).to.equal(1);
      expect((await repo.listOwned(org, me, { skip: 0, limit: 10, search: '.*' })).total).to.equal(0);
    });

    it('updateOwned applies the patch, bumps __v when an array is replaced, and returns the new row', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const app = await repo.create(mk(org, me));
      const renamed = await repo.updateOwned(org, me, app._id, { name: 'Renamed', accessTokenLifetime: 60 });
      expect(renamed).to.include({ name: 'Renamed', accessTokenLifetime: 60 });
      expect(renamed?.__v).to.equal(app.__v);
      const scoped = await repo.updateOwned(org, me, app._id, {
        allowedScopes: ['org:read', 'kb:read'],
        redirectUris: ['https://b.example/cb'],
      });
      expect(scoped?.allowedScopes).to.deep.equal(['org:read', 'kb:read']);
      expect(scoped?.redirectUris).to.deep.equal(['https://b.example/cb']);
      expect(scoped!.__v).to.be.greaterThan(app.__v);
      expect((await repo.findOwned(org, me, app._id))?.name).to.equal('Renamed');
    });

    it('updateOwned clears a field given undefined, and the key is absent afterwards', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const svc = fx.newId();
      const app = await repo.create(mk(org, me, { homepageUrl: 'https://h.example' }));
      const set = await repo.updateOwned(org, me, app._id, { tokenIdentityUserId: svc });
      expect(set?.tokenIdentityUserId).to.equal(svc);
      const cleared = await repo.updateOwned(org, me, app._id, { tokenIdentityUserId: undefined, homepageUrl: undefined });
      expect(cleared).to.not.have.property('tokenIdentityUserId');
      expect(cleared).to.not.have.property('homepageUrl');
      const keys = await fx.rawKeys(app._id);
      expect(keys).to.not.include('tokenIdentityUserId');
      expect(keys).to.not.include('homepageUrl');
    });

    it('a knowledge limit is stored with the app, replaced by a patch and cleared by undefined', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const limit = { apps: ['a-1'], recordGroups: [], records: ['r-1', 'r-2'] };
    const app = await repo.create(mk(org, me, { knowledgeLimit: limit }));
    expect(app.knowledgeLimit).to.deep.equal(limit);
    expect((await repo.findLiveAllowedScopes(app.clientId))?.knowledgeLimit).to.deep.equal(limit);
    const next = { apps: [], recordGroups: ['g-1'], records: [] };
    expect((await repo.updateOwned(org, me, app._id, { knowledgeLimit: next }))?.knowledgeLimit).to.deep.equal(next);
    const cleared = await repo.updateOwned(org, me, app._id, { knowledgeLimit: undefined });
    expect(cleared).to.not.have.property('knowledgeLimit');
    expect(await repo.findLiveAllowedScopes(app.clientId)).to.not.have.property('knowledgeLimit');
    expect(await repo.create(mk(org, me))).to.not.have.property('knowledgeLimit');
  });

  it('updateOwned on an app the caller does not own changes nothing and returns null', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const app = await repo.create(mk(org, me));
      expect(await repo.updateOwned(org, fx.newId(), app._id, { name: 'Hijack' })).to.equal(null);
      expect(await repo.updateOwned(fx.newId(), me, app._id, { name: 'Hijack' })).to.equal(null);
      expect((await repo.findOwned(org, me, app._id))?.name).to.equal('App');
    });

    it('soft delete keeps the row but hides it from owned reads and from live lookups', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const app = await repo.create(mk(org, me));
      const gone = await repo.updateOwned(org, me, app._id, { isDeleted: true, deletedBy: me, status: OAuthAppStatus.REVOKED });
      expect(gone).to.include({ isDeleted: true, status: OAuthAppStatus.REVOKED, deletedBy: me });
      expect(await repo.findOwned(org, me, app._id)).to.equal(null);
      expect(await repo.findLiveByClientId(app.clientId)).to.equal(null);
      expect((await repo.findByClientId(app.clientId))?._id).to.equal(app._id);
    });
  });

  describe('lookups by client id (the org is not known yet)', () => {
    it('findLiveByClientId finds a live app whatever its status, never a deleted one', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const app = await repo.create(mk(org, me));
      await repo.updateOwned(org, me, app._id, { status: OAuthAppStatus.SUSPENDED });
      const found = await repo.findLiveByClientId(app.clientId);
      expect(found?._id).to.equal(app._id);
      expect(found?.status).to.equal(OAuthAppStatus.SUSPENDED);
      expect(await repo.findLiveByClientId('no-such-client')).to.equal(null);
    });

    it('findLiveAllowedScopes returns only the scopes of a live app', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const app = await repo.create(mk(org, me, { allowedScopes: ['a', 'b'] }));
      expect(await repo.findLiveAllowedScopes(app.clientId)).to.deep.equal({ allowedScopes: ['a', 'b'] });
      await repo.updateOwned(org, me, app._id, { isDeleted: true });
      expect(await repo.findLiveAllowedScopes(app.clientId)).to.equal(null);
    });

    it('create keeps the service account the app is created for', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const svc = fx.newId();
      const app = await repo.create({ ...mk(org, me), tokenIdentityUserId: svc });
      expect(app.tokenIdentityUserId).to.equal(svc);
      expect(await repo.findLiveIdentity(app.clientId)).to.deep.include({ createdBy: me, tokenIdentityUserId: svc });
    });

    it('findLiveIdentity: the creator, or the account the app is pointed at; none once deleted', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const svc = fx.newId();
      const app = await repo.create(mk(org, me));
      expect(await repo.findLiveIdentity(app.clientId)).to.deep.include({ createdBy: me });
      expect(await repo.findLiveIdentity(app.clientId)).to.not.have.property('tokenIdentityUserId');
      await repo.updateOwned(org, me, app._id, { tokenIdentityUserId: svc });
      expect(await repo.findLiveIdentity(app.clientId)).to.deep.include({ createdBy: me, tokenIdentityUserId: svc });
      await repo.updateOwned(org, me, app._id, { isDeleted: true });
      expect(await repo.findLiveIdentity(app.clientId)).to.equal(null);
      expect(await repo.findLiveIdentity('unknown')).to.equal(null);
    });

    it('a client id is matched as a value, never as an operator', async () => {
      await repo.create(mk(fx.newId(), fx.newId()));
      for (const call of [
        () => repo.findLiveByClientId({ $ne: 'x' } as unknown as string),
        () => repo.findLiveAllowedScopes({ $ne: 'x' } as unknown as string),
        () => repo.findByClientId({ $ne: 'x' } as unknown as string),
      ]) {
        try {
          expect(await call()).to.equal(null);
        } catch (e) {
          expect((e as Error).name).to.not.equal('AssertionError');
        }
      }
    });
  });

  describe('apps tied to a person', () => {
    it('listClientIdsActingAs lists live apps pointed at the account', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const svc = fx.newId();
      const a = await repo.create(mk(org, me));
      const b = await repo.create(mk(org, me));
      await repo.create(mk(org, me));
      await repo.updateOwned(org, me, a._id, { tokenIdentityUserId: svc });
      await repo.updateOwned(org, me, b._id, { tokenIdentityUserId: svc });
      await repo.updateOwned(org, me, b._id, { isDeleted: true });
      expect(await repo.listClientIdsActingAs(svc)).to.deep.equal([a.clientId]);
      expect(await repo.listClientIdsActingAs(fx.newId())).to.deep.equal([]);
    });

    it('listClientIdsCreatedBy and softDeleteCreatedBy cover every live app of the user, including internal ones', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const admin = fx.newId();
      const mine = await repo.create(mk(org, me));
      const pat = await repo.create(mk(org, me, { clientId: `pat-system:${org}` }));
      const other = await repo.create(mk(org, fx.newId()));
      const elsewhere = await repo.create(mk(fx.newId(), me));
      expect((await repo.listClientIdsCreatedBy(org, me)).sort()).to.deep.equal([mine.clientId, pat.clientId].sort());
      await repo.softDeleteCreatedBy(org, me, admin);
      expect(await repo.listClientIdsCreatedBy(org, me)).to.deep.equal([]);
      const row = await repo.findByClientId(mine.clientId);
      expect(row).to.include({ isDeleted: true, status: OAuthAppStatus.REVOKED, deletedBy: admin });
      expect((await repo.findByClientId(other.clientId))?.isDeleted).to.equal(false);
      expect((await repo.findByClientId(elsewhere.clientId))?.isDeleted).to.equal(false);
    });
  });
}
