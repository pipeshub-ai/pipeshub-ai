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
const TEAM_A = '3f2b8c1e-5a4d-4e6f-8a9b-0c1d2e3f4a5b';
const TEAM_B = '9a8b7c6d-1e2f-4a3b-9c4d-5e6f7a8b9c0d';
const ALL_TEAM = `all_${ORG.toString()}`;
const LEGACY_TEAM = oid();

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

  describe('PI-20: list, detail and getAccessibleProjectIds share one predicate', () => {
    const corpus: Row[] = [
      project({ userId: ME }),
      project({ members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] }),
      project({ members: [{ principalType: 'user', principalId: oid(), role: 'editor' }] }),
      project({ members: [{ principalType: 'team', teamId: TEAM_A, role: 'editor' }] }),
      project({ members: [{ principalType: 'team', teamId: TEAM_B, role: 'editor' }] }),
      project({ members: [{ principalType: 'team', teamId: ALL_TEAM, role: 'viewer' }] }),
      project({ members: [{ principalType: 'team', principalId: LEGACY_TEAM, role: 'viewer' }] }),
      project({ visibility: 'org' }),
      project({ orgId: OTHER_ORG, visibility: 'org' }),
      project({ orgId: OTHER_ORG, members: [{ principalType: 'team', teamId: TEAM_A, role: 'editor' }] }),
      project({ isDeleted: true, userId: ME }),
      project(),
    ];

    for (const [name, teams] of [
      ['no teams', []],
      ['one UUID team', [TEAM_A]],
      ['UUID, all-org and legacy ObjectId teams', [TEAM_A, ALL_TEAM, LEGACY_TEAM.toString()]],
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

    it('a UUID team member sees the project via list (was dropped by the ObjectId filter)', () => {
      const p = project({ members: [{ principalType: 'team', teamId: TEAM_A, role: 'viewer' }] });
      const filter = { orgId: ORG, isDeleted: false, $or: projectAccessFilter(subject([TEAM_A]), 'shared') };
      expect(matchesFilter(p, filter)).to.equal(true);
    });

    it('scope "mine" never matches by membership', () => {
      const p = project({ members: [{ principalType: 'user', principalId: ME, role: 'viewer' }] });
      expect(matchesFilter(p, { $or: projectAccessFilter(subject([]), 'mine') })).to.equal(false);
    });

    it('a user row and a team row on different members do not combine in one $elemMatch', () => {
      const p = project({
        members: [
          { principalType: 'user', principalId: oid(), role: 'viewer' },
          { principalType: 'team', teamId: TEAM_B, role: 'viewer' },
        ],
      });
      expect(matchesFilter(p, { $or: projectAccessFilter(subject([TEAM_A]), 'shared') })).to.equal(false);
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
      const row = project({ members: [{ principalType: 'team', teamId: TEAM_A, role: 'editor' }] });
      sinon.stub(Project, 'findOne').resolves(row as any);
      const found = await adapter.roleOf(subject([TEAM_A]), String(row._id));
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
      expect(await adapter.accessibleProjectIds(subject([TEAM_A]))).to.deep.equal([id.toString()]);
    });
  });

  describe('project member schema', () => {
    const base = { orgId: ORG, userId: OWNER, name: 'p' };
    const validate = (member: Row) => new Project({ ...base, members: [{ addedBy: OWNER, ...member }] }).validateSync();

    it('accepts a team row with teamId and no principalId', () => {
      expect(validate({ principalType: 'team', teamId: TEAM_A, role: 'viewer' })).to.equal(undefined);
    });

    it('accepts a legacy team row with an ObjectId principalId', () => {
      expect(validate({ principalType: 'team', principalId: LEGACY_TEAM, role: 'viewer' })).to.equal(undefined);
    });

    it('rejects a user row without principalId and a team row with neither key', () => {
      expect(validate({ principalType: 'user', role: 'viewer' })).to.not.equal(undefined);
      expect(validate({ principalType: 'team', role: 'viewer' })).to.not.equal(undefined);
    });
  });
});
