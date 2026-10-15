import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { BadRequestError, ForbiddenError, NotFoundError } from '../../../src/libs/errors/http.errors';
import { Subject } from '../../../src/modules/authz/domain/types';
import { projectAccessFilter } from '../../../src/modules/authz/loaders/project.loader';
import { Project } from '../../../src/modules/projects/schema/project.schema';
import { ProjectServiceAccessAdapter } from '../../../src/modules/projects/services/project-access.adapter';
import { ProjectService } from '../../../src/modules/projects/services/project.service';
import { matchesFilter } from '../enterprise_search/controller/chat-test-harness';

const oid = () => new mongoose.Types.ObjectId();
const ORG = oid();
const OTHER_ORG = oid();
const ME = oid();
const OWNER = oid();
const TEAM_A = oid();
const TEAM_B = oid();

type Row = Record<string, any>;

const project = (overrides: Row = {}): Row => ({
  _id: oid(),
  orgId: ORG,
  userId: OWNER,
  visibility: 'private',
  isDeleted: false,
  members: [],
  ...overrides,
});

const subject = (teamIds: Subject['teamIds']): Subject => ({
  userId: ME.toString(),
  orgId: ORG.toString(),
  teamIds,
});

describe('projects behind IProjectAccessPort', () => {
  afterEach(() => sinon.restore());

  describe('list, detail and getAccessibleProjectIds share one predicate', () => {
    const corpus: Row[] = [
      project({ userId: ME }),
      project({ members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] }),
      project({ members: [{ principalType: 'user', principalId: oid(), role: 'editor' }] }),
      project({ members: [{ principalType: 'team', principalId: TEAM_A, role: 'editor' }] }),
      project({ members: [{ principalType: 'team', principalId: TEAM_B, role: 'viewer' }] }),
      project({ visibility: 'org' }),
      project({ orgId: OTHER_ORG, visibility: 'org' }),
      project({ orgId: OTHER_ORG, members: [{ principalType: 'team', principalId: TEAM_A, role: 'editor' }] }),
      project({ isDeleted: true, userId: ME }),
      project(),
    ];

    for (const [name, teams] of [
      ['no teams', []],
      ['one team', [TEAM_A.toString()]],
      ['two teams', [TEAM_A.toString(), TEAM_B.toString()]],
      ['unresolved', 'unresolved' as const],
    ] as Array<[string, Subject['teamIds']]>) {
      it(`matches exactly the projects whose role is not none (${name})`, () => {
        const s = subject(teams);
        const callerTeamIds = teams === 'unresolved' ? [] : [...teams];
        const filter = { orgId: ORG, isDeleted: false, $or: projectAccessFilter(s, 'all') };
        const listed = corpus.filter((p) => matchesFilter(p, filter)).map((p) => p._id.toString());
        const byRole = corpus
          .filter((p) => !p.isDeleted && ProjectService.computeRole(p as any, s.userId, s.orgId, callerTeamIds) !== 'none')
          .map((p) => p._id.toString());
        expect(listed).to.have.members(byRole);
      });
    }

    it('scope "mine" never matches by membership', () => {
      const p = project({ members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] });
      expect(matchesFilter(p, { $or: projectAccessFilter(subject([]), 'mine') })).to.equal(false);
    });

    it('a user row and a team row on different members do not combine in one $elemMatch', () => {
      const p = project({
        members: [
          { principalType: 'user', principalId: oid(), role: 'viewer' },
          { principalType: 'team', principalId: TEAM_B, role: 'viewer' },
        ],
      });
      expect(matchesFilter(p, { $or: projectAccessFilter(subject([TEAM_A.toString()]), 'shared') })).to.equal(false);
    });
  });

  describe('ProjectServiceAccessAdapter', () => {
    const adapter = new ProjectServiceAccessAdapter();

    it('roleOf returns null for a missing project, an invalid id, and a project the subject cannot see', async () => {
      const stub = sinon.stub(Project, 'findOne');
      stub.onFirstCall().resolves(null);
      expect(await adapter.roleOf(subject([]), oid().toString())).to.equal(null);
      expect(await adapter.roleOf(subject([]), 'nope')).to.equal(null);
      stub.onSecondCall().resolves(project() as any);
      expect(await adapter.roleOf(subject([]), oid().toString())).to.equal(null);
    });

    it('roleOf resolves the role through a team row', async () => {
      const row = project({ members: [{ principalType: 'team', principalId: TEAM_A, role: 'editor' }] });
      sinon.stub(Project, 'findOne').resolves(row as any);
      const found = await adapter.roleOf(subject([TEAM_A.toString()]), String(row._id));
      expect(found?.role).to.equal('editor');
    });

    it('assertAtLeast maps hidden to NotFound, too-low to Forbidden, bad id to BadRequest', async () => {
      const row = project({ members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] });
      const stub = sinon.stub(Project, 'findOne');
      stub.resolves(row as any);
      expect((await adapter.assertAtLeast(subject([]), String(row._id), 'viewer')) as any).to.equal(row);
      let caught: unknown;
      try {
        await adapter.assertAtLeast(subject([]), String(row._id), 'editor');
      } catch (e) {
        caught = e;
      }
      expect(caught).to.be.instanceOf(ForbiddenError);
      stub.resolves(null);
      try {
        await adapter.assertAtLeast(subject([]), oid().toString(), 'viewer');
      } catch (e) {
        caught = e;
      }
      expect(caught).to.be.instanceOf(NotFoundError);
      try {
        await adapter.assertAtLeast(subject([]), 'bad', 'viewer');
      } catch (e) {
        caught = e;
      }
      expect(caught).to.be.instanceOf(BadRequestError);
    });

    it('accessibleProjectIds returns string ids from the shared filter', async () => {
      const id = oid();
      sinon.stub(Project, 'find').returns({ lean: sinon.stub().resolves([{ _id: id }]) } as any);
      expect(await adapter.accessibleProjectIds(subject([TEAM_A.toString()]))).to.deep.equal([id.toString()]);
    });
  });

  describe('project member schema', () => {
    const base = { orgId: ORG, userId: OWNER, name: 'p' };
    const validate = (member: Row) => new Project({ ...base, members: [{ addedBy: OWNER, ...member }] }).validateSync();

    it('accepts a team row with an ObjectId principalId', () => {
      expect(validate({ principalType: 'team', principalId: TEAM_A, role: 'viewer' })).to.equal(undefined);
    });

    it('rejects a user or team row without principalId', () => {
      expect(validate({ principalType: 'user', role: 'viewer' })).to.not.equal(undefined);
      expect(validate({ principalType: 'team', role: 'viewer' })).to.not.equal(undefined);
    });
  });
});
