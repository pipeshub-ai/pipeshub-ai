import { expect } from 'chai';
import { Types } from 'mongoose';
import {
  listFilter,
  readFilter,
  writeFilter,
} from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.filters';
import { Caller } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types';
import { matchesFilter } from '../controller/chat-test-harness';

const oid = () => new Types.ObjectId();
const org = oid();
const owner = oid();
const u1 = oid();
const u2 = oid();
const stranger = oid();

const caller = (userId: Types.ObjectId, teamIds: Caller['teamIds'] = []): Caller => ({
  userId: userId.toString(),
  orgId: org.toString(),
  teamIds,
});
const chat = { kind: 'chat' } as const;
const readOpts = { accessibleProjectIds: [], includeOwned: true, includeShared: true };

const doc = (over: Record<string, unknown> = {}) => ({
  orgId: org,
  userId: owner,
  isDeleted: false,
  sessionType: 'chat',
  isShared: true,
  sharedWith: [
    { userId: u1, accessLevel: 'read' },
    { userId: u2, accessLevel: 'write' },
  ],
  ...over,
});
const matches = (d: Record<string, unknown>, f: object) => matchesFilter(d, f as never);

describe('conversation access filters', () => {
  it('SEC-02: a read-only collaborator matches the read filter but not the write filter', () => {
    expect(matches(doc(), readFilter(caller(u1), chat, readOpts))).to.equal(true);
    expect(matches(doc(), writeFilter(caller(u1), chat, { collab: true }))).to.equal(false);
    expect(matches(doc(), writeFilter(caller(u2), chat, { collab: true }))).to.equal(true);
  });

  it('the write filter matches only the owner while the flag is off', () => {
    expect(matches(doc(), writeFilter(caller(u2), chat, { collab: false }))).to.equal(false);
    expect(matches(doc(), writeFilter(caller(owner), chat, { collab: false }))).to.equal(true);
  });

  it('PH03-04: a legacy row without principalType matches the user branch', () => {
    expect(matches(doc(), readFilter(caller(u1), chat, readOpts))).to.equal(true);
  });

  it('PH03-04: a team row matches only a caller in that team', () => {
    const d = doc({ sharedWith: [{ principalType: 'team', teamId: 't1', accessLevel: 'read' }] });
    expect(matches(d, readFilter(caller(u1, ['t1']), chat, readOpts))).to.equal(true);
    expect(matches(d, readFilter(caller(u1, ['t2']), chat, readOpts))).to.equal(false);
    expect(matches(d, readFilter(caller(u1, 'unresolved'), chat, readOpts))).to.equal(false);
  });

  it('SEC-03: isShared alone never matches a stranger', () => {
    expect(matches(doc(), readFilter(caller(stranger), chat, readOpts))).to.equal(false);
    expect(matches(doc({ sharedWith: [] }), readFilter(caller(stranger), chat, readOpts))).to.equal(false);
  });

  it('does not let a user row match through a team id, or the reverse', () => {
    const d = doc({ sharedWith: [{ userId: u1, teamId: 'tX', accessLevel: 'read' }] });
    expect(matches(d, readFilter(caller(stranger, ['tX']), chat, readOpts))).to.equal(false);
  });

  it('scopes by org, deleted flag and kind', () => {
    const f = readFilter(caller(owner), chat, readOpts);
    expect(matches(doc({ orgId: oid() }), f)).to.equal(false);
    expect(matches(doc({ isDeleted: true }), f)).to.equal(false);
    expect(matches(doc({ sessionType: 'agent' }), f)).to.equal(false);
    const agent = readFilter(caller(owner), { kind: 'agent', agentKey: 'a1' }, readOpts);
    expect(matches(doc({ sessionType: 'agent', agentKey: 'a1' }), agent)).to.equal(true);
    expect(matches(doc({ sessionType: 'agent', agentKey: 'a2' }), agent)).to.equal(false);
  });

  it('matches a chat shared with the project only through an accessible project', () => {
    const projectId = oid();
    const d = doc({ sharedWith: [], projectId, projectVisibility: 'project' });
    expect(matches(d, readFilter(caller(stranger), chat, { ...readOpts, accessibleProjectIds: [projectId.toString()] }))).to.equal(true);
    expect(matches(d, readFilter(caller(stranger), chat, readOpts))).to.equal(false);
    expect(matches({ ...d, projectVisibility: 'private' }, readFilter(caller(stranger), chat, { ...readOpts, accessibleProjectIds: [projectId.toString()] }))).to.equal(false);
  });

  it('wraps the access clause in $and so appended conditions cannot overwrite it (F-17)', () => {
    const f = readFilter(caller(u1), chat, readOpts) as { $and: unknown[] };
    expect(f.$and).to.have.length(1);
    expect(f).to.not.have.property('$or');
    expect(listFilter(caller(u1), chat, readOpts)).to.have.property('$or');
  });

  it('PH12-01: each principal kind is its own $or branch with its own $elemMatch, and no $or sits inside an $elemMatch', () => {
    const f = listFilter(caller(u1, ['t1', 't2']), chat, { ...readOpts, includeOwned: false }) as { $or: Array<{ sharedWith: { $elemMatch: Record<string, unknown> } }> };
    expect(f.$or).to.have.length(2);
    const [user, team] = f.$or.map((branch) => branch.sharedWith.$elemMatch);
    expect(user).to.deep.equal({ userId: u1 });
    expect(team).to.deep.equal({ userId: null, teamId: { $in: ['t1', 't2'] } });
    expect(JSON.stringify(f.$or)).to.not.include('$or');
  });

  it('PH12-01: the level test stays when the filter does not cover every level, so a team row cannot grant write to a reader', () => {
    const write = writeFilter(caller(u1, ['t1']), chat, { collab: true }) as { $and: Array<{ $or: Array<{ sharedWith?: { $elemMatch: Record<string, unknown> } }> }> };
    const rows = write.$and[0]!.$or.flatMap((branch) => (branch.sharedWith ? [branch.sharedWith.$elemMatch] : []));
    expect(rows).to.deep.equal([
      { userId: u1, accessLevel: { $in: ['write'] } },
      { userId: null, teamId: { $in: ['t1'] }, accessLevel: { $in: ['write'] } },
    ]);
    const readerViaTeam = doc({ sharedWith: [{ teamId: 't1', accessLevel: 'read' }] });
    expect(matches(readerViaTeam, write)).to.equal(false);
    expect(matches(doc({ sharedWith: [{ teamId: 't1', accessLevel: 'write' }] }), write)).to.equal(true);
  });

  it('matches nothing when no branch applies', () => {
    const f = readFilter(caller(u1), chat, { ...readOpts, includeOwned: false, includeShared: false });
    expect(matches(doc(), f)).to.equal(false);
  });

  it('narrows to one conversation', () => {
    const id = oid();
    const f = readFilter(caller(owner), { ...chat, conversationId: id.toString() }, readOpts);
    expect(matches(doc({ _id: id }), f)).to.equal(true);
    expect(matches(doc({ _id: oid() }), f)).to.equal(false);
  });

  it('rejects an agent filter without an agentKey', () => {
    expect(() => readFilter(caller(owner), { kind: 'agent' }, readOpts)).to.throw();
  });

  it('kind any matches chat and agent sessions of the caller', () => {
    const f = readFilter(caller(owner), { kind: 'any' }, readOpts);
    expect(matches(doc(), f)).to.equal(true);
    expect(matches(doc({ sessionType: 'agent', agentKey: 'a1' }), f)).to.equal(true);
  });

  it('without share rows, a direct recipient matches only through the project', () => {
    const projectId = oid();
    const opts = { ...readOpts, accessibleProjectIds: [projectId.toString()], includeShareRows: false };
    const f = readFilter(caller(u1), { kind: 'any' }, opts);
    expect(matches(doc(), f)).to.equal(false);
    expect(matches(doc({ projectId, projectVisibility: 'project' }), f)).to.equal(true);
    expect(matches(doc(), readFilter(caller(u1), { kind: 'any' }, { ...opts, includeShareRows: true }))).to.equal(true);
  });

  describe('LC-21: chats the caller left', () => {
    const teamShared = (over: Record<string, unknown> = {}) =>
      doc({ sharedWith: [{ principalType: 'team', teamId: 't1', accessLevel: 'read' }], hiddenFor: [u1], ...over });

    it('a list hides a chat the caller left even though a team still shares it', () => {
      const opts = { ...readOpts, archived: 'exclude', excludeHidden: true } as const;
      expect(matches(teamShared(), readFilter(caller(u1, ['t1']), chat, opts))).to.equal(false);
      expect(matches(teamShared(), listFilter(caller(u1, ['t1']), chat, opts))).to.equal(false);
      expect(matches(teamShared({ hiddenFor: [u2] }), readFilter(caller(u1, ['t1']), chat, opts))).to.equal(true);
    });

    it('the archived list hides it too, so Leave is not undone through Archive', () => {
      const opts = { ...readOpts, archived: 'only', excludeHidden: true } as const;
      expect(matches(teamShared({ archivedFor: [u1] }), readFilter(caller(u1, ['t1']), chat, opts))).to.equal(false);
    });

    it('a by-id read still matches through the team', () => {
      expect(matches(teamShared(), readFilter(caller(u1, ['t1']), { ...chat, conversationId: oid().toString() }, readOpts))).to.equal(false);
      const id = oid();
      expect(matches(teamShared({ _id: id }), readFilter(caller(u1, ['t1']), { ...chat, conversationId: id.toString() }, readOpts))).to.equal(true);
    });
  });

  describe('archived scope', () => {
    const onlyOpts = { ...readOpts, archived: 'only' } as const;
    const excludeOpts = { ...readOpts, archived: 'exclude' } as const;

    it('exclude hides what the caller archived and nothing else', () => {
      const f = readFilter(caller(u1), chat, excludeOpts);
      expect(matches(doc(), f)).to.equal(true);
      expect(matches(doc({ archivedFor: [u1] }), f)).to.equal(false);
      expect(matches(doc({ archivedFor: [u2] }), f)).to.equal(true);
    });

    it('only lists the caller per-user archives and the owner global archives', () => {
      expect(matches(doc({ archivedFor: [u1] }), readFilter(caller(u1), chat, onlyOpts))).to.equal(true);
      expect(matches(doc({ archivedFor: [u2] }), readFilter(caller(u1), chat, onlyOpts))).to.equal(false);
      expect(matches(doc(), readFilter(caller(u1), chat, onlyOpts))).to.equal(false);
      const globallyArchived = doc({ isArchived: true });
      expect(matches(globallyArchived, readFilter(caller(owner), chat, onlyOpts))).to.equal(true);
      expect(matches(globallyArchived, readFilter(caller(u1), chat, onlyOpts))).to.equal(false);
    });

    it('without a scope the filter is unchanged', () => {
      expect(readFilter(caller(u1), chat, readOpts)).to.deep.equal(readFilter(caller(u1), chat, { ...readOpts, archived: undefined }));
      expect((readFilter(caller(u1), chat, readOpts) as { $and: unknown[] }).$and).to.have.length(1);
    });

    it('the list variant keeps the access $or at the top level', () => {
      const f = listFilter(caller(u1), chat, excludeOpts) as Record<string, unknown>;
      expect(f).to.have.property('$or');
      expect(matches(doc({ archivedFor: [u1] }), f)).to.equal(false);
      expect(matches(doc(), f)).to.equal(true);
    });
  });
});
