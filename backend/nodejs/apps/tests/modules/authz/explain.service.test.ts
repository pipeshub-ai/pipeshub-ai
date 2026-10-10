import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ExplainService } from '../../../src/modules/authz/explain.service';
import { Subject } from '../../../src/modules/authz/domain/types';

const ORG = 'o1';
const subj = (userId: string, teamIds: Subject['teamIds'] = []): Subject => ({ userId, orgId: ORG, teamIds });
const ref = { type: 'chat', id: 'c1' } as const;
const explanation = {
  role: 'editor' as const,
  via: [
    { type: 'direct' as const, ref: 'target', role: 'viewer' as const },
    { type: 'team' as const, ref: 'team-9', role: 'editor' as const },
  ],
  teamsUnresolved: false,
};

const build = (opts: { admin?: boolean; ownerId?: string | null } = {}) => {
  const authz = { check: sinon.stub(), explain: sinon.stub().resolves(explanation) };
  const chats = {
    load: sinon.stub().resolves(opts.ownerId === null ? null : { session: { userId: { toString: () => opts.ownerId ?? 'owner' } }, project: null }),
  };
  const isAdmin = sinon.stub().resolves(opts.admin ?? false);
  return { service: new ExplainService(authz as any, chats as any, isAdmin), authz, chats, isAdmin };
};

describe('ExplainService (PI-21, PI-22)', () => {
  describe('PI-21: self', () => {
    it('returns every path with roles, with no owner or admin lookup', async () => {
      const { service, chats, isAdmin } = build();
      const out = await service.explain(subj('target', ['team-9']), subj('target', ['team-9']), ref);
      expect(out.via).to.deep.equal(explanation.via);
      expect(out.role).to.equal('editor');
      expect(chats.load.called).to.equal(false);
      expect(isAdmin.called).to.equal(false);
    });
  });

  describe('PI-22: others', () => {
    it('forbids a non-owner non-admin', async () => {
      const { service, authz } = build({ ownerId: 'someone-else' });
      let err: any;
      try { await service.explain(subj('nosy'), subj('target'), ref); } catch (e) { err = e; }
      expect(err?.statusCode).to.equal(403);
      expect(authz.explain.called).to.equal(false);
    });

    it('forbids identically when the chat does not exist (no id probing)', async () => {
      const { service } = build({ ownerId: null });
      let err: any;
      try { await service.explain(subj('nosy'), subj('target'), ref); } catch (e) { err = e; }
      expect(err?.statusCode).to.equal(403);
    });

    it('allows the resource owner, redacting teams the owner is not in', async () => {
      const { service, authz } = build({ ownerId: 'boss' });
      const out = await service.explain(subj('boss', ['team-1']), subj('target'), ref);
      expect(authz.explain.firstCall.args[0].userId).to.equal('target');
      expect(out.via[1]).to.deep.equal({ type: 'team', ref: 'redacted', role: 'editor' });
      expect(out.via[0]).to.deep.equal(explanation.via[0]);
    });

    it('allows an org admin and keeps team refs the admin belongs to', async () => {
      const { service } = build({ admin: true });
      const out = await service.explain(subj('adm', ['team-9']), subj('target'), ref);
      expect(out.via[1].ref).to.equal('team-9');
    });

    it('forbids explaining a user from another org', async () => {
      const { service } = build({ admin: true });
      let err: any;
      try { await service.explain(subj('adm'), { ...subj('target'), orgId: 'o2' }, ref); } catch (e) { err = e; }
      expect(err?.statusCode).to.equal(403);
    });
  });
});
