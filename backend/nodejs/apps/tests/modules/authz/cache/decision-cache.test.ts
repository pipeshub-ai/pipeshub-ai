import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { DecisionCache, buildDecisionKey } from '../../../../src/modules/authz/cache/decision-cache';
import { ACL_VERSION_INC, bumpAclVersionOnDoc, readAclVersion } from '../../../../src/modules/authz/cache/acl-version';

const parts = { orgId: 'o', subjectKey: 'user:u', resourceType: 'chat', resourceId: 'c', aclVersion: 3, teamsVersion: 7 };

describe('authz decision cache', () => {
  it('builds the versioned key and defaults missing versions to 0', () => {
    expect(buildDecisionKey(parts)).to.equal('authz:v1:o:user:u:chat:c:3:7');
    expect(buildDecisionKey({ ...parts, aclVersion: undefined, teamsVersion: null })).to.equal('authz:v1:o:user:u:chat:c:0:0');
  });

  it('serves from the per-request map and misses for another request', async () => {
    const cache = new DecisionCache<string>();
    const r1 = {};
    await cache.set(r1, parts, 'viewer');
    expect(await cache.get(r1, parts)).to.equal('viewer');
    expect(await cache.get({}, parts)).to.equal(undefined);
  });

  it('a bumped aclVersion or teamsVersion is a miss (no stale decision)', async () => {
    const shared = { get: sinon.stub().resolves(null), set: sinon.stub().resolves() };
    const cache = new DecisionCache<string>({ cache: shared as any });
    const r = {};
    await cache.set(r, parts, 'editor');
    expect(await cache.get(r, { ...parts, aclVersion: 4 })).to.equal(undefined);
    expect(await cache.get(r, { ...parts, teamsVersion: 8 })).to.equal(undefined);
    expect(shared.set.firstCall.args[2]).to.deep.equal({ ttl: 30 });
  });

  it('reads through the shared cache and memoizes per request', async () => {
    const shared = { get: sinon.stub().resolves('viewer'), set: sinon.stub() };
    const cache = new DecisionCache<string>({ cache: shared as any });
    const r = {};
    expect(await cache.get(r, parts)).to.equal('viewer');
    await cache.get(r, parts);
    expect(shared.get.calledOnce).to.equal(true);
  });

  it('resolves a cache supplied as a function on every call', async () => {
    const shared = { get: sinon.stub().resolves('viewer'), set: sinon.stub().resolves() };
    let current: typeof shared | undefined;
    const cache = new DecisionCache<string>({ cache: () => current as any });
    expect(await cache.get({}, parts)).to.equal(undefined);
    current = shared;
    expect(await cache.get({}, parts)).to.equal('viewer');
    await cache.set({}, parts, 'viewer');
    expect(shared.set.calledOnce).to.equal(true);
  });

  it('never caches a decision derived from unresolved teams', async () => {
    const shared = { get: sinon.stub().resolves(null), set: sinon.stub().resolves() };
    const cache = new DecisionCache<string>({ cache: shared as any });
    const r = {};
    await cache.set(r, parts, 'none', { teamsResolved: false });
    expect(shared.set.called).to.equal(false);
    expect(await cache.get(r, parts)).to.equal(undefined);
  });

  it('treats shared-cache errors as a miss, never a throw', async () => {
    const warn = sinon.stub();
    const shared = { get: sinon.stub().rejects(new Error('down')), set: sinon.stub().rejects(new Error('down')) };
    const cache = new DecisionCache<string>({ cache: shared as any, logger: { warn } });
    const r = {};
    expect(await cache.get(r, parts)).to.equal(undefined);
    await cache.set(r, parts, 'viewer');
    expect(await cache.get(r, parts)).to.equal('viewer');
    expect(warn.callCount).to.be.greaterThan(1);
  });
});

describe('aclVersion helpers', () => {
  it('treats missing as 0 and bumps docs', () => {
    expect(readAclVersion(undefined)).to.equal(0);
    expect(readAclVersion({})).to.equal(0);
    const d: { aclVersion?: number } = {};
    bumpAclVersionOnDoc(d);
    bumpAclVersionOnDoc(d);
    expect(d.aclVersion).to.equal(2);
    expect(ACL_VERSION_INC).to.deep.equal({ $inc: { aclVersion: 1 } });
  });
});
