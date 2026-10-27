/**
 * Contract of the remaining repositories: org, project, connectors config,
 * Slack thread, delegation, org metrics job, and the user/group lookups added
 * with them. Store-neutral: only the repository interfaces are used.
 */
import { expect } from 'chai';
import type { NewOrgIds, NewOrgWithAdmin, OrgRepository } from '../../../src/libs/repositories/org/types';
import type { ProjectRepository } from '../../../src/libs/repositories/project/types';
import type { ConnectorsConfigRepository } from '../../../src/libs/repositories/connectors-config/types';
import type { SlackThreadRepository } from '../../../src/libs/repositories/slack-thread/types';
import type { DelegationRepository } from '../../../src/libs/repositories/delegation/types';
import type { OrgMetricsJobRepository } from '../../../src/libs/repositories/org-metrics-job/types';
import type { UserRepository } from '../../../src/libs/repositories/user/types';
import type { GroupRepository } from '../../../src/libs/repositories/group/types';
import type { CredentialRepository } from '../../../src/libs/repositories/credential/types';
import type { OrgAuthConfigRepository } from '../../../src/libs/repositories/org-auth-config/types';
import type { Tx, UnitOfWork } from '../../../src/libs/repositories/uow';

/** What the organisation, metrics and lookup suites need. */
export interface OrgFixture {
  orgs: OrgRepository;
  metrics: OrgMetricsJobRepository;
  users: UserRepository;
  groups: GroupRepository;
  credentials: CredentialRepository;
  orgAuth: OrgAuthConfigRepository;
  uow: UnitOfWork;
  transactional: boolean;
  newId(): string;
  /** Runs `fn` as the organisation being created (PostgreSQL binds its context from the ids; Mongo needs nothing). */
  asNewOrg<T>(ids: NewOrgIds, fn: () => Promise<T>): Promise<T>;
  /** An organisation with a live account of its own that the calls may run as (PostgreSQL only needs it for the metrics suite). */
  withJobs?: boolean;
}

export interface RestFixture extends OrgFixture {
  projects: ProjectRepository;
  connectors: ConnectorsConfigRepository;
  slack: SlackThreadRepository;
  delegation: DelegationRepository;
}

let n = 0;
const uniq = (p: string): string => `${p}${process.pid}x${Date.now()}x${n++}`;

/**
 * Creates an organisation the way sign-up does: ids first, then the creation unit as that organisation (the administrator's first
 * password is stored in that same unit). `inside` runs in the unit after the creation.
 */
export async function createOrgLikeSignUp(
  fx: OrgFixture,
  input: Omit<NewOrgWithAdmin, 'ids'>,
  inside?: (tx: Tx, created: Awaited<ReturnType<OrgRepository['createWithAdmin']>>) => Promise<void>,
): Promise<Awaited<ReturnType<OrgRepository['createWithAdmin']>>> {
  const ids = fx.orgs.allocateIds();
  const created = await fx.asNewOrg(ids, () =>
    fx.uow.run(async (tx) => {
      const r = await fx.orgs.createWithAdmin({ ...input, ids }, tx);
      await inside?.(tx, r);
      return r;
    }),
  );
  return created;
}

export function describeRestContract(fx: RestFixture): void {
  const { projects, connectors, slack, delegation, users, groups } = fx;
  describeOrgContract(fx);
  describeMetricsContract(fx);
  describeLookupContract(fx);
  describeRemainingContract(fx, { projects, connectors, slack, delegation, users, groups });
}

const orgInput = (extra: Record<string, unknown> = {}): Omit<NewOrgWithAdmin, 'ids'> => ({
  org: {
    registeredName: uniq('Acme'),
    domain: 'acme.example.com',
    contactEmail: `${uniq('o')}@acme.example.com`,
    accountType: 'business',
    onBoardingStatus: 'notConfigured',
    ...extra,
  },
  admin: { fullName: 'Admin One', email: `${uniq('a')}@Acme.example.com` },
  passwordHash: 'hash-value-1',
  ipAddress: '10.0.0.1',
});

export function describeOrgContract(fx: OrgFixture): void {
  const { orgs, users, groups } = fx;
  const newOrg = (extra: Record<string, unknown> = {}) => createOrgLikeSignUp(fx, orgInput(extra));

  describe('org', () => {
    it('creates the organisation with its administrator, groups and sign-in config', async () => {
      const { org, adminUserId, adminEmail } = await newOrg();
      expect(org._id).to.be.a('string');
      expect(org.slug).to.match(/^org-\d+$/);
      expect(org.isDeleted).to.equal(false);
      expect(org.__v).to.equal(0);
      expect(adminEmail).to.equal(adminEmail.toLowerCase());
      const admin = await users.getLive(org._id, adminUserId);
      expect(admin?.role).to.equal('admin');
      expect(admin?.email).to.equal(adminEmail);
      const mine = await groups.groupsOfUser(org._id, adminUserId);
      expect(mine.map((g) => g.type)).to.have.members(['everyone']);
      expect((await orgs.getLive(org._id))?.onBoardingStatus).to.equal('notConfigured');
      const cfg = await fx.orgAuth.getLive(org._id);
      expect(cfg?.authSteps[0].allowedMethods[0].type).to.equal('password');
      const cred = await fx.credentials.find(adminUserId, org._id);
      expect(cred?.hashedPassword).to.equal('hash-value-1');
    });

    it('gives each organisation the next slug number', async () => {
      const a = await newOrg();
      const b = await newOrg();
      const num = (s?: string): number => Number((s ?? '').replace('org-', ''));
      expect(num(b.org.slug)).to.equal(num(a.org.slug) + 1);
    });

    it('rolls everything back when the unit of work fails (replica set); keeps partial writes otherwise', async () => {
      const email = `${uniq('rb')}@acme.example.com`;
      let createdOrg = '';
      await createOrgLikeSignUp(
        fx,
        {
          org: { registeredName: 'RB', domain: 'acme.example.com', contactEmail: email, accountType: 'business' },
          admin: { fullName: 'RB', email },
          passwordHash: 'hash-value',
          ipAddress: '10.0.0.2',
        },
        async (_tx, r) => {
          createdOrg = r.org._id;
          throw new Error('boom');
        },
      ).catch((e: Error) => {
        expect(e.message).to.equal('boom');
      });
      const found = await orgs.getLive(createdOrg);
      if (fx.transactional) expect(found).to.equal(null);
      else expect(found).to.not.equal(null);
    });

    it('reads, updates and soft-deletes', async () => {
      const { org } = await newOrg({ shortName: 'AC' });
      expect(await orgs.getAccountType(org._id)).to.include({ accountType: 'business' });
      expect(await orgs.getNames(org._id)).to.include({ shortName: 'AC' });
      expect((await orgs.getOnboardingStatus(org._id))?.onBoardingStatus).to.equal('notConfigured');
      const set = await orgs.setOnboardingStatus(org._id, 'configured');
      expect(set?.onBoardingStatus).to.equal('configured');
      const upd = await orgs.update(org._id, { shortName: 'NEW' });
      expect(upd?.shortName).to.equal('NEW');
      expect(await orgs.update(fx.newId(), { shortName: 'x' })).to.equal(null);
      expect(await orgs.count()).to.be.greaterThan(0);
      expect(await orgs.getFirstLive()).to.not.equal(null);
      expect(await orgs.isActive(org._id)).to.equal(true);
      expect(await orgs.isActive(fx.newId())).to.equal(false);
      expect((await orgs.liveSummary()).count).to.be.greaterThan(0);
      const gone = await orgs.softDelete(org._id);
      expect(gone?.isDeleted).to.equal(true);
      expect(await orgs.getLive(org._id)).to.equal(null);
      expect(await orgs.softDelete(org._id)).to.equal(null);
      expect(await orgs.getAccountType(org._id)).to.equal(null);
      expect(await orgs.setOnboardingStatus(org._id, 'skipped')).to.equal(null);
    });

    it('stores, replaces and empties the logo', async () => {
      const { org } = await newOrg();
      expect(await orgs.getLogo(org._id)).to.equal(null);
      expect(await orgs.removeLogo(org._id)).to.equal(null);
      await orgs.setLogo(org._id, 'AAA', 'image/png');
      await orgs.setLogo(org._id, 'BBB', 'image/jpeg');
      const logo = await orgs.getLogo(org._id);
      expect(logo).to.include({ logo: 'BBB', mimeType: 'image/jpeg' });
      const emptied = await orgs.removeLogo(org._id);
      expect(emptied?.logo).to.equal(null);
      expect((await orgs.getLogo(org._id))?.logo).to.equal(null);
    });
  });
}

export function describeRemainingContract(
  fx: OrgFixture,
  r: { projects: ProjectRepository; connectors: ConnectorsConfigRepository; slack: SlackThreadRepository; delegation: DelegationRepository; users: UserRepository; groups: GroupRepository },
): void {
  const { projects, connectors, slack, delegation } = r;

  describe('project', () => {
    const base = { tools: [] as string[] };
    it('creates with defaults and string ids', async () => {
      const org = fx.newId();
      const owner = fx.newId();
      const p = await projects.create(org, owner, { ...base, name: 'Alpha', instructions: 'be nice' });
      expect(p).to.include({ orgId: org, userId: owner, name: 'Alpha', visibility: 'private', chatSharing: 'private', isPinned: false, isArchived: false, isDeleted: false });
      expect(p.members).to.deep.equal([]);
      expect(p.linkedKnowledgeBaseId).to.equal(null);
      expect(await projects.getLive(p._id)).to.deep.include({ _id: p._id, name: 'Alpha' });
      expect(await projects.getDeleted(p._id)).to.equal(null);
    });

    it('lists by scope, pinned first, filters archived, escapes search, pages', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const other = fx.newId();
      const team = fx.newId();
      const mine = await projects.create(org, me, { ...base, name: 'Mine (v1)' });
      const shared = await projects.create(org, other, { ...base, name: 'Shared' });
      const viaTeam = await projects.create(org, other, { ...base, name: 'ViaTeam' });
      const open = await projects.create(org, other, { ...base, name: 'Open' });
      await projects.create(org, other, { ...base, name: 'Hidden' });
      await projects.upsertMembers(shared._id, [{ principalId: me, principalType: 'user', role: 'viewer' }], other);
      await projects.upsertMembers(viaTeam._id, [{ principalId: team, principalType: 'team', role: 'viewer' }], other);
      await projects.update(open._id, { visibility: 'org' });
      await projects.setPinned(mine._id, me, true, false);
      const q = { includeArchived: false, teamIds: [team, 'not-an-id'], skip: 0, limit: 50 };
      const names = async (scope: 'mine' | 'shared' | 'all', extra: Record<string, unknown> = {}) =>
        (await projects.list(org, me, { ...q, scope, ...extra })).rows.map((r) => r.name);
      expect(await names('mine')).to.deep.equal(['Mine (v1)']);
      expect(await names('shared')).to.have.members(['Shared', 'ViaTeam']);
      const all = await names('all');
      expect(all[0]).to.equal('Mine (v1)');
      expect(all).to.have.members(['Mine (v1)', 'Shared', 'ViaTeam', 'Open']);
      expect(await names('all', { search: '(v1)' })).to.deep.equal(['Mine (v1)']);
      const page = await projects.list(org, me, { ...q, scope: 'all', skip: 1, limit: 2 });
      expect(page.rows).to.have.length(2);
      expect(page.total).to.equal(4);
      await projects.update(shared._id, { isArchived: true, archivedBy: me });
      expect(await names('shared')).to.deep.equal(['ViaTeam']);
      expect(await names('shared', { includeArchived: true })).to.have.members(['Shared', 'ViaTeam']);
      expect(await names('shared', { isArchived: true })).to.deep.equal(['Shared']);
      const none = await projects.list(fx.newId(), me, { ...q, scope: 'all' });
      expect(none.total).to.equal(0);
    });

    it('updates only the given fields; a key set to undefined clears the stored value', async () => {
      const owner = fx.newId();
      const p = await projects.create(fx.newId(), owner, { ...base, name: 'U', description: 'd' });
      const a = await projects.update(p._id, { isArchived: true, archivedBy: owner, tools: ['t'] });
      expect(a).to.include({ isArchived: true, archivedBy: owner, description: 'd' });
      expect(a?.tools).to.deep.equal(['t']);
      const b = await projects.update(p._id, { isArchived: false, archivedBy: undefined });
      expect(b?.isArchived).to.equal(false);
      expect(b).to.not.have.property('archivedBy');
      expect(await projects.update(fx.newId(), { name: 'x' })).to.equal(null);
    });

    it('deletes softly and then is only found as deleted', async () => {
      const owner = fx.newId();
      const p = await projects.create(fx.newId(), owner, { ...base, name: 'D' });
      const gone = await projects.markDeleted(p._id, owner);
      expect(gone).to.include({ isDeleted: true, deletedBy: owner });
      expect(await projects.getLive(p._id)).to.equal(null);
      expect((await projects.getDeleted(p._id))?._id).to.equal(p._id);
      expect(await projects.markDeleted(p._id, owner)).to.equal(null);
      expect(await projects.update(p._id, { name: 'again' })).to.equal(null);
    });

    it('pins per person: a pin moves the project only in the list of whoever pinned it', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const other = fx.newId();
      const a = await projects.create(org, me, { ...base, name: 'A' });
      const b = await projects.create(org, me, { ...base, name: 'B' });
      const c = await projects.create(org, me, { ...base, name: 'C' });
      await projects.touchActivity(a._id);
      await projects.touchActivity(b._id);
      await projects.touchActivity(c._id); // most recent activity first: C, B, A
      for (const p of [a, b, c]) await projects.upsertMembers(p._id, [{ principalId: other, principalType: 'user', role: 'viewer' }], me);
      const q = { scope: 'all' as const, includeArchived: false, teamIds: [], skip: 0, limit: 50 };
      const order = async (who: string) => (await projects.list(org, who, q)).rows.map((r) => r.name);
      expect(await order(me)).to.deep.equal(['C', 'B', 'A']);

      const pinned = await projects.setPinned(a._id, other, true, false);
      expect(pinned?.pinnedBy).to.deep.equal([other]);
      expect(await order(other)).to.deep.equal(['A', 'C', 'B']);
      expect(await order(me)).to.deep.equal(['C', 'B', 'A']);

      await projects.setPinned(a._id, other, true, false); // pinning twice stores one pin
      expect((await projects.getLive(a._id))?.pinnedBy).to.deep.equal([other]);
      await projects.setPinned(a._id, me, true, false);
      expect((await projects.getLive(a._id))?.pinnedBy).to.have.members([me, other]);

      const unpinned = await projects.setPinned(a._id, other, false, false);
      expect(unpinned?.pinnedBy).to.deep.equal([me]);
      expect(await order(other)).to.deep.equal(['C', 'B', 'A']);
      expect(await projects.setPinned(fx.newId(), me, true, false)).to.equal(null);
      await projects.markDeleted(b._id, me);
      expect(await projects.setPinned(b._id, me, true, false)).to.equal(null);
    });

    it('matches a team member by its graph id string, as stored', async () => {
      const org = fx.newId();
      const owner = fx.newId();
      const person = fx.newId();
      const teamId = '5d2a9c4e-1c0b-4a77-9d3e-0b1f6a2c7e11'; // a graph id is not an ObjectId
      const allOrg = `all_${org}`;
      const viaTeam = await projects.create(org, owner, { ...base, name: 'ViaTeam' });
      const viaAll = await projects.create(org, owner, { ...base, name: 'ViaAllOrg' });
      const unrelated = await projects.create(org, owner, { ...base, name: 'Unrelated' });
      await projects.upsertMembers(viaTeam._id, [{ principalId: teamId, principalType: 'team', role: 'editor' }], owner);
      await projects.upsertMembers(viaAll._id, [{ principalId: allOrg, principalType: 'team', role: 'viewer' }], owner);
      expect((await projects.getLive(viaTeam._id))?.members.map((m) => [m.principalType, m.principalId])).to.deep.equal([['team', teamId]]);

      const q = { scope: 'shared' as const, includeArchived: false, skip: 0, limit: 50 };
      const names = async (teamIds: string[]) => (await projects.list(org, person, { ...q, teamIds })).rows.map((r) => r.name);
      expect(await names([])).to.deep.equal([]);
      expect(await names([teamId])).to.deep.equal(['ViaTeam']);
      expect(await names([teamId, allOrg])).to.have.members(['ViaTeam', 'ViaAllOrg']);

      expect(await projects.listAccessibleIds(org, person, [])).to.deep.equal([]);
      expect((await projects.listAccessibleIds(org, person, [teamId, allOrg])).sort()).to.deep.equal([viaTeam._id, viaAll._id].sort());
      expect(unrelated._id).to.be.a('string');

      const removed = await projects.removeMember(viaTeam._id, teamId, 'team');
      expect(removed?.members).to.deep.equal([]);
    });

    it('tells whether an organisation has a team among its project members, and whether a project has none', async () => {
      const org = fx.newId();
      const owner = fx.newId();
      const withTeam = await projects.create(org, owner, { ...base, name: 'WithTeam' });
      const plain = await projects.create(org, owner, { ...base, name: 'Plain' });
      expect(await projects.orgHasTeamMembers(org)).to.equal(false);
      expect(await projects.isLiveWithoutTeamMembers(plain._id)).to.equal(true);
      await projects.upsertMembers(plain._id, [{ principalId: fx.newId(), principalType: 'user', role: 'viewer' }], owner);
      expect(await projects.isLiveWithoutTeamMembers(plain._id)).to.equal(true);

      await projects.upsertMembers(withTeam._id, [{ principalId: 'team-graph-id', principalType: 'team', role: 'viewer' }], owner);
      expect(await projects.orgHasTeamMembers(org)).to.equal(true);
      expect(await projects.isLiveWithoutTeamMembers(withTeam._id)).to.equal(false);

      await projects.markDeleted(withTeam._id, owner);
      expect(await projects.orgHasTeamMembers(org)).to.equal(false);
      expect(await projects.isLiveWithoutTeamMembers(withTeam._id)).to.equal(false);
      expect(await projects.isLiveWithoutTeamMembers(fx.newId())).to.equal(false);
    });

    it('adds members, changes roles, never stores the owner, and removes by type', async () => {
      const org = fx.newId();
      const owner = fx.newId();
      const u = fx.newId();
      const t = fx.newId();
      const p = await projects.create(org, owner, { ...base, name: 'M' });
      const a = await projects.upsertMembers(
        p._id,
        [
          { principalId: owner, principalType: 'user', role: 'editor' },
          { principalId: u, principalType: 'user', role: 'viewer' },
          { principalId: t, principalType: 'team', role: 'viewer' },
        ],
        owner,
      );
      expect(a?.members.map((m) => `${m.principalType}:${m.principalId}`)).to.have.members([`user:${u}`, `team:${t}`]);
      expect(a?.members[0].addedBy).to.equal(owner);
      const b = await projects.upsertMembers(p._id, [{ principalId: u, principalType: 'user', role: 'editor' }], owner);
      expect(b?.members.find((m) => m.principalId === u)?.role).to.equal('editor');
      expect(b?.members).to.have.length(2);
      const c = await projects.removeMember(p._id, u, 'team'); // wrong type: nothing removed
      expect(c?.members).to.have.length(2);
      const d = await projects.removeMember(p._id, u, 'user');
      expect(d?.members).to.have.length(1);
      expect(await projects.upsertMembers(fx.newId(), [], owner)).to.equal(null);
      expect(await projects.removeMember(fx.newId(), u, 'user')).to.equal(null);
    });

    it('finds projects with a linked collection for a member, pulls a person out, lists accessible ids', async () => {
      const org = fx.newId();
      const owner = fx.newId();
      const u = fx.newId();
      const linked = await projects.create(org, owner, { ...base, name: 'L' });
      const plain = await projects.create(org, owner, { ...base, name: 'P' });
      await projects.upsertMembers(linked._id, [{ principalId: u, principalType: 'user', role: 'viewer' }], owner);
      await projects.upsertMembers(plain._id, [{ principalId: u, principalType: 'user', role: 'viewer' }], owner);
      await projects.claimLinkedKb(linked._id, null, 'kb-1');
      const found = await projects.listWithLinkedKbForMember(org, u);
      expect(found.map((p) => p._id)).to.deep.equal([linked._id]);
      expect((await projects.listAccessibleIds(org, u, [])).sort()).to.deep.equal([linked._id, plain._id].sort());
      expect(await projects.listAccessibleIds(org, fx.newId(), [])).to.deep.equal([]);
      await projects.removeUserFromAll(org, u);
      expect((await projects.getLive(linked._id))?.members).to.deep.equal([]);
      expect(await projects.listAccessibleIds(org, u, [])).to.deep.equal([]);
    });

    it('links a collection for only one of several racing callers', async () => {
      const p = await projects.create(fx.newId(), fx.newId(), { ...base, name: 'R' });
      const results = await Promise.all(['k1', 'k2', 'k3', 'k4'].map((k) => projects.claimLinkedKb(p._id, null, k)));
      expect(results.filter(Boolean)).to.have.length(1);
      const winner = results.find(Boolean);
      expect((await projects.getLive(p._id))?.linkedKnowledgeBaseId).to.equal(winner?.linkedKnowledgeBaseId);
      expect(await projects.claimLinkedKb(p._id, 'wrong', 'k9')).to.equal(null);
      expect((await projects.claimLinkedKb(p._id, winner?.linkedKnowledgeBaseId ?? null, 'k9'))?.linkedKnowledgeBaseId).to.equal('k9');
    });

    it('bumps the activity time', async () => {
      const p = await projects.create(fx.newId(), fx.newId(), { ...base, name: 'T' });
      await new Promise((r) => setTimeout(r, 5));
      await projects.touchActivity(p._id);
      expect((await projects.getLive(p._id))!.lastActivityAt).to.be.greaterThan(p.lastActivityAt);
    });
  });

  describe('connectors config', () => {
    it('creates enabled, finds, and enables again', async () => {
      const org = fx.newId();
      const u1 = fx.newId();
      const u2 = fx.newId();
      expect(await connectors.find(org, 'Google Workspace')).to.equal(null);
      expect(await connectors.enable(org, 'Google Workspace', u1)).to.equal(null);
      const created = await connectors.createEnabled(org, 'Google Workspace', u1);
      expect(created).to.include({ orgId: org, name: 'Google Workspace', isEnabled: true, lastUpdatedBy: u1 });
      const again = await connectors.enable(org, 'Google Workspace', u2);
      expect(again).to.include({ isEnabled: true, lastUpdatedBy: u2, _id: created!._id });
      expect(await connectors.find(fx.newId(), 'Google Workspace')).to.equal(null);
    });
  });

  describe('slack thread', () => {
    it('upserts one link per thread, bot and person', async () => {
      const t = uniq('t');
      expect(await slack.getConversationId(t, 'b1', 'a@x.io')).to.equal(null);
      await slack.saveConversationId({ threadId: t, botId: 'b1', email: 'a@x.io', conversationId: 'c1' });
      await slack.saveConversationId({ threadId: t, botId: 'b1', email: 'a@x.io', conversationId: 'c2' });
      await slack.saveConversationId({ threadId: t, botId: 'b2', email: 'a@x.io', conversationId: 'c3' });
      expect(await slack.getConversationId(t, 'b1', 'a@x.io')).to.equal('c2');
      expect(await slack.getConversationId(t, 'b2', 'a@x.io')).to.equal('c3');
      expect(await slack.getConversationId(t, 'b1', 'other@x.io')).to.equal(null);
    });
  });

  describe('delegation', () => {
    it('grants once, counts, lists newest first, revokes', async () => {
      const org = fx.newId();
      const sa = fx.newId();
      const s1 = fx.newId();
      const s2 = fx.newId();
      const by = fx.newId();
      expect(await delegation.isAllowed(org, sa, s1)).to.equal(false);
      const g1 = await delegation.grant({ orgId: org, serviceAccountId: sa, subjectUserId: s1, grantedBy: by, reason: 'r' });
      expect(g1.created).to.equal(true);
      expect(g1.row).to.include({ orgId: org, serviceAccountId: sa, subjectUserId: s1, grantedBy: by, reason: 'r' });
      const dup = await delegation.grant({ orgId: org, serviceAccountId: sa, subjectUserId: s1, grantedBy: fx.newId() });
      expect(dup.created).to.equal(false);
      expect(dup.row._id).to.equal(g1.row._id);
      await new Promise((r) => setTimeout(r, 5));
      await delegation.grant({ orgId: org, serviceAccountId: sa, subjectUserId: s2, grantedBy: by });
      expect(await delegation.countFor(org, sa)).to.equal(2);
      expect((await delegation.listFor(org, sa)).map((r) => r.subjectUserId)).to.deep.equal([s2, s1]);
      expect(await delegation.isAllowed(org, sa, s1)).to.equal(true);
      expect(await delegation.isAllowed(fx.newId(), sa, s1)).to.equal(false);
      expect(await delegation.revoke(org, sa, s1)).to.equal(true);
      expect(await delegation.revoke(org, sa, s1)).to.equal(false);
      expect(await delegation.revokeAllFor(org, sa)).to.equal(1);
      expect(await delegation.countFor(org, sa)).to.equal(0);
    });

    it('records events and lists them newest first for an organisation or one account', async () => {
      const org = fx.newId();
      const sa1 = fx.newId();
      const sa2 = fx.newId();
      await delegation.recordEvent({ orgId: org, serviceAccountId: sa1, subjectUserId: fx.newId(), requestedSubject: 'a'.repeat(400), outcome: 'granted', scopes: ['s'] });
      await new Promise((r) => setTimeout(r, 5));
      await delegation.recordEvent({ orgId: org, serviceAccountId: sa2, requestedSubject: 'b@x.io', outcome: 'refused', refusedReason: 'not_on_allow_list' });
      const all = await delegation.listEvents(org, undefined, 10);
      expect(all.map((e) => e.outcome)).to.deep.equal(['refused', 'granted']);
      expect(all[1].requestedSubject).to.have.length(320);
      expect(all[1].scopes).to.deep.equal(['s']);
      expect(all[0].scopes).to.deep.equal([]);
      expect((await delegation.listEvents(org, sa1, 10)).map((e) => e.outcome)).to.deep.equal(['granted']);
      expect(await delegation.listEvents(org, undefined, 1)).to.have.length(1);
      expect(await delegation.listEvents(fx.newId(), undefined, 10)).to.deep.equal([]);
    });
  });
}

export function describeMetricsContract(fx: OrgFixture): void {
  const { orgs, metrics, users } = fx;
  const newOrg = (extra: Record<string, unknown> = {}) => createOrgLikeSignUp(fx, orgInput(extra));

  describe('org metrics job', () => {
    it('counts live accounts per organisation and lists live organisations and sign-in configs', async () => {
      const { org, adminUserId } = await newOrg();
      await users.create(org._id, { email: `${uniq('m')}@x.io`, fullName: 'M' });
      const removed = await users.create(org._id, { email: `${uniq('r')}@x.io`, fullName: 'R' });
      await users.markDeleted(org._id, removed._id);
      const counts = await metrics.countUsersByOrg();
      expect(counts.find((c) => c._id === org._id)?.count).to.equal(2);
      const live = await metrics.listLiveOrgs();
      expect(live.find((o) => o._id === org._id)).to.include({ accountType: 'business', domain: 'acme.example.com' });
      const cfgs = await metrics.listLiveAuthConfigs();
      const mine = cfgs.find((c) => c.orgId === org._id);
      expect(mine?.authSteps?.[0].allowedMethods?.[0].type).to.equal('password');
      await orgs.softDelete(org._id);
      expect((await metrics.listLiveOrgs()).some((o) => o._id === org._id)).to.equal(false);
      expect(adminUserId).to.be.a('string');
    });
  });
}

export function describeLookupContract(fx: OrgFixture): void {
  const { users, groups } = fx;
  const newOrg = (extra: Record<string, unknown> = {}) => createOrgLikeSignUp(fx, orgInput(extra));

  describe('user and group lookups added with these repositories', () => {
    it('answers field-limited reads', async () => {
      const { org, adminUserId } = await newOrg();
      const svc = await users.create(org._id, { email: `${uniq('s')}@x.io`, fullName: 'Svc', kind: 'service' });
      const human = await users.create(org._id, { email: `${uniq('h')}@x.io`, fullName: 'Hu', firstName: 'H', lastName: 'U' });
      expect(await users.getLiveFields(org._id, svc._id, ['fullName', 'isDisabled'], { serviceOnly: true })).to.include({ fullName: 'Svc' });
      expect(await users.getLiveFields(org._id, human._id, ['fullName'], { serviceOnly: true })).to.equal(null);
      expect(await users.getLiveFields(org._id, human._id, ['fullName', 'role'])).to.include({ fullName: 'Hu' });
      expect(await users.getLiveFields(fx.newId(), human._id, ['fullName'])).to.equal(null);
      expect(await users.getByIdOrEmail(org._id, { id: human._id }, ['email'])).to.include({ email: human.email });
      expect(await users.getByIdOrEmail(org._id, { email: human.email }, ['_id'])).to.include({ _id: human._id });
      expect(await users.getByIdOrEmail(fx.newId(), { email: human.email }, ['_id'])).to.equal(null);
      const listed = await users.listFieldsByIds([human._id, svc._id, fx.newId()], ['email']);
      expect(listed.map((u) => u.email)).to.have.members([human.email, svc.email]);
      expect(await users.getOldestId(org._id)).to.equal(adminUserId);
      expect(await users.getOldestId(fx.newId())).to.equal(null);
      await users.markDeleted(org._id, human._id);
      expect((await users.getAnyById(human._id))?._id).to.equal(human._id);
      expect(await users.getAnyById(fx.newId())).to.equal(null);
      expect(await users.getLiveByEmail(org._id, human.email)).to.equal(null);
      expect((await users.getLiveByEmail(org._id, svc.email))?._id).to.equal(svc._id);
      expect(await users.getContact(svc._id)).to.include({ fullName: 'Svc', email: svc.email });
      const names = await users.listNamesByIds(org._id, [human._id, svc._id, adminUserId]);
      expect(names.map((u) => u._id)).to.have.members([svc._id, adminUserId]);
    });

    it('lists member ids of the live groups of the given types', async () => {
      const { org, adminUserId } = await newOrg();
      const custom = await groups.create(org._id, { name: uniq('c'), type: 'custom' });
      const other = await users.create(org._id, { email: `${uniq('g')}@x.io`, fullName: 'G' });
      await groups.addMembers(org._id, [custom._id], [other._id]);
      expect(await groups.memberIdsOfTypes(org._id, ['everyone'])).to.deep.equal([adminUserId]);
      expect(await groups.memberIdsOfTypes(org._id, ['custom'])).to.deep.equal([other._id]);
      expect((await groups.memberIdsOfTypes(org._id, ['everyone', 'custom'])).sort()).to.deep.equal([adminUserId, other._id].sort());
      expect(await groups.memberIdsOfTypes(org._id, ['admin'])).to.deep.equal([]);
      await groups.softDelete(org._id, custom._id);
      expect(await groups.memberIdsOfTypes(org._id, ['custom'])).to.deep.equal([]);
      expect(await groups.memberIdsOfTypes(fx.newId(), ['everyone'])).to.deep.equal([]);
    });
  });
}
