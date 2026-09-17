import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ProjectService } from '../../../../src/modules/projects/services/project.service';
import { Project } from '../../../../src/modules/projects/schema/project.schema';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../../src/libs/errors/http.errors';

/** Awaits `promise`, asserts it rejects with an instance of `ErrorType`
 * (optionally matching `messagePattern`), and fails the test otherwise. */
async function expectRejection(
  promise: Promise<unknown>,
  ErrorType: new (...args: any[]) => Error,
  messagePattern?: RegExp,
): Promise<void> {
  try {
    await promise;
    expect.fail('Expected promise to reject');
  } catch (error: any) {
    expect(error).to.be.instanceOf(ErrorType);
    if (messagePattern) {
      expect(error.message).to.match(messagePattern);
    }
  }
}

const ORG_ID = new mongoose.Types.ObjectId().toString();
const OTHER_ORG_ID = new mongoose.Types.ObjectId().toString();
const OWNER_ID = new mongoose.Types.ObjectId().toString();
const MEMBER_ID = new mongoose.Types.ObjectId().toString();
const OUTSIDER_ID = new mongoose.Types.ObjectId().toString();

function makeProjectDoc(overrides: Record<string, any> = {}): any {
  const doc: any = {
    _id: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(ORG_ID),
    userId: new mongoose.Types.ObjectId(OWNER_ID),
    name: 'Test Project',
    description: undefined,
    icon: undefined,
    color: undefined,
    instructions: undefined,
    knowledgeScope: undefined,
    appliedFilters: undefined,
    tools: [],
    linkedKnowledgeBaseId: null,
    members: [],
    visibility: 'private',
    chatSharing: 'private',
    isPinned: false,
    isArchived: false,
    isDeleted: false,
    lastActivityAt: Date.now(),
    ...overrides,
  };
  doc.save = sinon.stub().resolvesThis();
  return doc;
}

describe('ProjectService', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('computeRole', () => {
    it('returns "none" when the project belongs to a different org', () => {
      const project = makeProjectDoc({ orgId: new mongoose.Types.ObjectId(OTHER_ORG_ID) });
      expect(ProjectService.computeRole(project, OWNER_ID, ORG_ID)).to.equal('none');
    });

    it('returns "owner" for the project creator', () => {
      const project = makeProjectDoc();
      expect(ProjectService.computeRole(project, OWNER_ID, ORG_ID)).to.equal('owner');
    });

    it('returns the member role for an explicit member', () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'editor',
          },
        ],
      });
      expect(ProjectService.computeRole(project, MEMBER_ID, ORG_ID)).to.equal('editor');
    });

    it('returns "viewer" for any org user when visibility is "org"', () => {
      const project = makeProjectDoc({ visibility: 'org' });
      expect(ProjectService.computeRole(project, OUTSIDER_ID, ORG_ID)).to.equal('viewer');
    });

    it('returns "none" for a non-member outsider on a private project', () => {
      const project = makeProjectDoc();
      expect(ProjectService.computeRole(project, OUTSIDER_ID, ORG_ID)).to.equal('none');
    });

    it('grants the role of a matching team member when callerTeamIds includes it', () => {
      const teamId = new mongoose.Types.ObjectId().toString();
      const project = makeProjectDoc({
        members: [
          { principalType: 'team', principalId: new mongoose.Types.ObjectId(teamId), role: 'editor' },
        ],
      });
      expect(ProjectService.computeRole(project, OUTSIDER_ID, ORG_ID, [teamId])).to.equal('editor');
    });

    it('does not grant team access when callerTeamIds is omitted or empty', () => {
      const teamId = new mongoose.Types.ObjectId().toString();
      const project = makeProjectDoc({
        members: [
          { principalType: 'team', principalId: new mongoose.Types.ObjectId(teamId), role: 'editor' },
        ],
      });
      expect(ProjectService.computeRole(project, OUTSIDER_ID, ORG_ID)).to.equal('none');
    });

    it('picks the highest-ranked role across a matching user row and a matching team row', () => {
      const teamId = new mongoose.Types.ObjectId().toString();
      const project = makeProjectDoc({
        members: [
          { principalType: 'user', principalId: new mongoose.Types.ObjectId(MEMBER_ID), role: 'viewer' },
          { principalType: 'team', principalId: new mongoose.Types.ObjectId(teamId), role: 'editor' },
        ],
      });
      expect(ProjectService.computeRole(project, MEMBER_ID, ORG_ID, [teamId])).to.equal('editor');
    });
  });

  describe('assertAccess', () => {
    it('throws BadRequestError for a malformed project id', async () => {
      await expectRejection(
        ProjectService.assertAccess(ORG_ID, OWNER_ID, 'not-an-object-id'),
        BadRequestError,
      );
    });

    it('throws NotFoundError when the project does not exist', async () => {
      sinon.stub(Project, 'findOne').resolves(null);
      await expectRejection(
        ProjectService.assertAccess(ORG_ID, OWNER_ID, new mongoose.Types.ObjectId().toString()),
        NotFoundError,
      );
    });

    it('throws NotFoundError (never Forbidden) for a caller below the required role', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.assertAccess(ORG_ID, OUTSIDER_ID, project._id.toString(), 'viewer'),
        NotFoundError,
      );
    });

    it('throws NotFoundError when role is present but below the required rank', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'viewer',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.assertAccess(ORG_ID, MEMBER_ID, project._id.toString(), 'editor'),
        NotFoundError,
      );
    });

    it('resolves with role and project for an authorized caller', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const result = await ProjectService.assertAccess(
        ORG_ID,
        OWNER_ID,
        project._id.toString(),
        'viewer',
      );
      expect(result.role).to.equal('owner');
      expect(result.project).to.equal(project);
    });

    it('never returns a project soft-deleted under it (filters isDeleted: false)', async () => {
      const findOneStub = sinon.stub(Project, 'findOne').resolves(null);
      await expectRejection(
        ProjectService.assertAccess(ORG_ID, OWNER_ID, new mongoose.Types.ObjectId().toString()),
        NotFoundError,
      );
      expect(findOneStub.firstCall.args[0]).to.deep.include({ isDeleted: false });
    });
  });

  describe('create', () => {
    it('throws BadRequestError for an empty/whitespace name', async () => {
      await expectRejection(
        ProjectService.create(ORG_ID, OWNER_ID, { name: '   ' }),
        BadRequestError,
      );
    });

    it('trims the name and persists the new project', async () => {
      sinon.stub(Project.prototype, 'save').resolvesThis();
      const project = await ProjectService.create(ORG_ID, OWNER_ID, {
        name: '  My Project  ',
        description: 'desc',
      });
      expect(project.name).to.equal('My Project');
      expect(project.description).to.equal('desc');
      expect(project.orgId.toString()).to.equal(ORG_ID);
      expect(project.userId.toString()).to.equal(OWNER_ID);
      expect(project.tools).to.deep.equal([]);
      expect(project.linkedKnowledgeBaseId).to.equal(null);
      expect(project.members).to.deep.equal([]);
    });

    it('persists an explicit tools list', async () => {
      sinon.stub(Project.prototype, 'save').resolvesThis();
      const project = await ProjectService.create(ORG_ID, OWNER_ID, {
        name: 'Scoped Project',
        tools: ['gmail.send_email', 'kb.search'],
      });
      expect(project.tools).to.deep.equal(['gmail.send_email', 'kb.search']);
    });
  });

  describe('list', () => {
    function stubFindChain(resolved: any[]): sinon.SinonStub {
      const chain: any = {
        sort: sinon.stub().returnsThis(),
        skip: sinon.stub().returnsThis(),
        limit: sinon.stub().returnsThis(),
        lean: sinon.stub().returnsThis(),
        exec: sinon.stub().resolves(resolved),
      };
      return sinon.stub(Project, 'find').returns(chain);
    }

    it('includes only the owner branch in scope "mine"', async () => {
      const findStub = stubFindChain([]);
      sinon.stub(Project, 'countDocuments').resolves(0);
      sinon.stub(ChatSession, 'aggregate').resolves([]);

      await ProjectService.list(ORG_ID, OWNER_ID, {
        page: 1,
        limit: 20,
        scope: 'mine',
        includeArchived: false,
      });

      const filter = findStub.firstCall.args[0];
      expect(filter.$or).to.have.lengthOf(1);
      expect(filter.$or[0]).to.have.property('userId');
      expect(filter.isArchived).to.equal(false);
    });

    it('includes owner + shared(user) + org branches in scope "all" with no callerTeamIds', async () => {
      const findStub = stubFindChain([]);
      sinon.stub(Project, 'countDocuments').resolves(0);
      sinon.stub(ChatSession, 'aggregate').resolves([]);

      await ProjectService.list(ORG_ID, OWNER_ID, {
        page: 1,
        limit: 20,
        scope: 'all',
        includeArchived: true,
      });

      const filter = findStub.firstCall.args[0];
      expect(filter.$or).to.have.lengthOf(3);
      expect(filter).to.not.have.property('isArchived');
    });

    it('adds a fourth team-membership branch in scope "all" when callerTeamIds is non-empty', async () => {
      const findStub = stubFindChain([]);
      sinon.stub(Project, 'countDocuments').resolves(0);
      sinon.stub(ChatSession, 'aggregate').resolves([]);
      const teamId = new mongoose.Types.ObjectId().toString();

      await ProjectService.list(
        ORG_ID,
        OWNER_ID,
        { page: 1, limit: 20, scope: 'all', includeArchived: true },
        [teamId],
      );

      const filter = findStub.firstCall.args[0];
      expect(filter.$or).to.have.lengthOf(4);
      const teamBranch = filter.$or.find((clause: any) => clause['members.principalType'] === 'team');
      expect(teamBranch['members.principalId'].$in.map((id: any) => id.toString())).to.deep.equal([teamId]);
    });

    it('applies a case-insensitive name regex when search is provided', async () => {
      const findStub = stubFindChain([]);
      sinon.stub(Project, 'countDocuments').resolves(0);
      sinon.stub(ChatSession, 'aggregate').resolves([]);

      await ProjectService.list(ORG_ID, OWNER_ID, {
        page: 1,
        limit: 20,
        scope: 'mine',
        includeArchived: false,
        search: 'road',
      });

      const filter = findStub.firstCall.args[0];
      expect(filter.name).to.deep.equal({ $regex: 'road', $options: 'i' });
    });

    it('enriches each project with role and conversationCount from the $group aggregate', async () => {
      const projectId = new mongoose.Types.ObjectId();
      stubFindChain([
        { _id: projectId, orgId: new mongoose.Types.ObjectId(ORG_ID), userId: new mongoose.Types.ObjectId(OWNER_ID), members: [], visibility: 'private' },
      ]);
      sinon.stub(Project, 'countDocuments').resolves(1);
      sinon.stub(ChatSession, 'aggregate').resolves([{ _id: projectId, count: 3 }]);

      const { projects, totalCount } = await ProjectService.list(ORG_ID, OWNER_ID, {
        page: 1,
        limit: 20,
        scope: 'mine',
        includeArchived: false,
      });

      expect(totalCount).to.equal(1);
      expect(projects[0]?.role).to.equal('owner');
      expect(projects[0]?.conversationCount).to.equal(3);
    });

    it('skips the aggregate entirely when there are no projects on the page', async () => {
      stubFindChain([]);
      sinon.stub(Project, 'countDocuments').resolves(0);
      const aggregateStub = sinon.stub(ChatSession, 'aggregate').resolves([]);

      await ProjectService.list(ORG_ID, OWNER_ID, {
        page: 1,
        limit: 20,
        scope: 'mine',
        includeArchived: false,
      });

      expect(aggregateStub.called).to.equal(false);
    });
  });

  describe('update', () => {
    it('throws BadRequestError when clearing the name to blank', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.update(ORG_ID, OWNER_ID, project._id.toString(), { name: '   ' }),
        BadRequestError,
      );
    });

    it('applies simple field patches and saves', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.update(ORG_ID, OWNER_ID, project._id.toString(), {
        name: '  Renamed  ',
        instructions: 'Be concise.',
      });
      expect(updated.name).to.equal('Renamed');
      expect(updated.instructions).to.equal('Be concise.');
      expect(project.save.called).to.equal(true);
    });

    it('throws ForbiddenError when a non-owner editor tries to change visibility', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'editor',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.update(ORG_ID, MEMBER_ID, project._id.toString(), {
          visibility: 'org',
        }),
        ForbiddenError,
      );
    });

    it('allows the owner to change visibility and chatSharing', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.update(ORG_ID, OWNER_ID, project._id.toString(), {
        visibility: 'org',
        chatSharing: 'members',
      });
      expect(updated.visibility).to.equal('org');
      expect(updated.chatSharing).to.equal('members');
    });
  });

  describe('setPinned / setArchived', () => {
    it('toggles isPinned for at-least-editor callers', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.setPinned(ORG_ID, OWNER_ID, project._id.toString(), true);
      expect(updated.isPinned).to.equal(true);
    });

    it('sets archivedBy on archive and clears it on unarchive', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const archived = await ProjectService.setArchived(ORG_ID, OWNER_ID, project._id.toString(), true);
      expect(archived.isArchived).to.equal(true);
      expect(archived.archivedBy?.toString()).to.equal(OWNER_ID);

      const unarchived = await ProjectService.setArchived(ORG_ID, OWNER_ID, project._id.toString(), false);
      expect(unarchived.isArchived).to.equal(false);
      expect(unarchived.archivedBy).to.equal(undefined);
    });
  });

  describe('softDelete', () => {
    const originalRs = process.env.REPLICA_SET_AVAILABLE;

    afterEach(() => {
      process.env.REPLICA_SET_AVAILABLE = originalRs;
    });

    it('throws ForbiddenError for a non-owner (editor) caller', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'editor',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.softDelete(ORG_ID, MEMBER_ID, project._id.toString()),
        ForbiddenError,
      );
    });

    it('is idempotent — a no-op when already deleted', async () => {
      const project = makeProjectDoc({ isDeleted: true });
      sinon.stub(Project, 'findOne').resolves(project);
      const updateManyStub = sinon.stub(ChatSession, 'updateMany').resolves({} as any);

      await ProjectService.softDelete(ORG_ID, OWNER_ID, project._id.toString());

      expect(updateManyStub.called).to.equal(false);
      expect(project.save.called).to.equal(false);
    });

    it('unlinks sessions before marking the project deleted (non-replica-set path)', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const updateManyStub = sinon.stub(ChatSession, 'updateMany').resolves({} as any);

      await ProjectService.softDelete(ORG_ID, OWNER_ID, project._id.toString());

      expect(updateManyStub.calledBefore(project.save)).to.equal(true);
      expect(updateManyStub.firstCall.args[0]).to.deep.equal({ projectId: project._id });
      expect(updateManyStub.firstCall.args[1]).to.deep.equal({
        $unset: { projectId: '', projectVisibility: '' },
      });
      expect(project.isDeleted).to.equal(true);
      expect(project.deletedBy?.toString()).to.equal(OWNER_ID);
    });
  });

  describe('buildContext', () => {
    it('omits instructions when blank and passes through knowledgeScope, tools, and linkedKnowledgeBaseId', () => {
      const project = makeProjectDoc({
        instructions: '',
        knowledgeScope: { apps: ['app-1'] },
        tools: ['gmail.send_email'],
        linkedKnowledgeBaseId: 'hidden-kb-1',
      });
      const context = ProjectService.buildContext(project);
      expect(context.instructions).to.equal(undefined);
      expect(context.knowledgeScope).to.deep.equal({ apps: ['app-1'] });
      expect(context.tools).to.deep.equal(['gmail.send_email']);
      expect(context.linkedKnowledgeBaseId).to.equal('hidden-kb-1');
    });

    it('defaults tools to [] and linkedKnowledgeBaseId to null when unset', () => {
      const context = ProjectService.buildContext(makeProjectDoc({ tools: undefined, linkedKnowledgeBaseId: undefined }));
      expect(context.tools).to.deep.equal([]);
      expect(context.linkedKnowledgeBaseId).to.equal(null);
    });

    it('includes instructions when non-blank', () => {
      const project = makeProjectDoc({ instructions: 'Always cite sources.' });
      const context = ProjectService.buildContext(project);
      expect(context.instructions).to.equal('Always cite sources.');
    });

    it('trims instructions and treats whitespace-only as absent', () => {
      const trimmed = ProjectService.buildContext(
        makeProjectDoc({ instructions: '  Always cite sources.  ' }),
      );
      expect(trimmed.instructions).to.equal('Always cite sources.');

      const blank = ProjectService.buildContext(
        makeProjectDoc({ instructions: '   ' }),
      );
      expect(blank.instructions).to.equal(undefined);
    });
  });

  describe('upsertMembers', () => {
    it('throws ForbiddenError when the caller is not the owner', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'editor',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.upsertMembers(ORG_ID, MEMBER_ID, project._id.toString(), [
          { principalId: OUTSIDER_ID, role: 'viewer' },
        ]),
        ForbiddenError,
      );
    });

    it('never adds the owner as a member row', async () => {
      const project = makeProjectDoc();
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.upsertMembers(ORG_ID, OWNER_ID, project._id.toString(), [
        { principalId: OWNER_ID, role: 'editor' },
      ]);
      expect(updated.members).to.have.lengthOf(0);
    });

    it('adds a new member and updates the role of an existing one', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'viewer',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.upsertMembers(ORG_ID, OWNER_ID, project._id.toString(), [
        { principalId: MEMBER_ID, role: 'editor' },
        { principalId: OUTSIDER_ID, role: 'viewer' },
      ]);
      expect(updated.members).to.have.lengthOf(2);
      const existing: any = updated.members.find(
        (m: any) => m.principalId.toString() === MEMBER_ID,
      );
      expect(existing?.role).to.equal('editor');
      const added: any = updated.members.find(
        (m: any) => m.principalId.toString() === OUTSIDER_ID,
      );
      expect(added?.role).to.equal('viewer');
      expect(added?.addedBy.toString()).to.equal(OWNER_ID);
    });

    it('adds a team member distinctly from a user member sharing the same principalId', async () => {
      const sharedId = new mongoose.Types.ObjectId().toString();
      const project = makeProjectDoc({
        members: [
          { principalType: 'user', principalId: new mongoose.Types.ObjectId(sharedId), role: 'viewer' },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.upsertMembers(ORG_ID, OWNER_ID, project._id.toString(), [
        { principalId: sharedId, principalType: 'team', role: 'editor' },
      ]);
      expect(updated.members).to.have.lengthOf(2);
      const userRow: any = updated.members.find((m: any) => m.principalType === 'user');
      const teamRow: any = updated.members.find((m: any) => m.principalType === 'team');
      expect(userRow?.role).to.equal('viewer');
      expect(teamRow?.role).to.equal('editor');
    });
  });

  describe('removeMember', () => {
    it('throws ForbiddenError for a non-owner caller', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'editor',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      await expectRejection(
        ProjectService.removeMember(ORG_ID, MEMBER_ID, project._id.toString(), MEMBER_ID),
        ForbiddenError,
      );
    });

    it('removes the matching member for the owner', async () => {
      const project = makeProjectDoc({
        members: [
          {
            principalType: 'user',
            principalId: new mongoose.Types.ObjectId(MEMBER_ID),
            role: 'editor',
          },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.removeMember(
        ORG_ID,
        OWNER_ID,
        project._id.toString(),
        MEMBER_ID,
      );
      expect(updated.members).to.have.lengthOf(0);
    });

    it('only removes the team row when principalType="team" is given, leaving a same-id user row intact', async () => {
      const sharedId = new mongoose.Types.ObjectId(MEMBER_ID);
      const project = makeProjectDoc({
        members: [
          { principalType: 'user', principalId: sharedId, role: 'editor' },
          { principalType: 'team', principalId: sharedId, role: 'viewer' },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.removeMember(
        ORG_ID,
        OWNER_ID,
        project._id.toString(),
        MEMBER_ID,
        'team',
      );
      expect(updated.members).to.have.lengthOf(1);
      expect((updated.members[0] as any)?.principalType).to.equal('user');
    });

    it('defaults to removing a "user" principalType when none is given', async () => {
      const project = makeProjectDoc({
        members: [
          { principalType: 'user', principalId: new mongoose.Types.ObjectId(MEMBER_ID), role: 'editor' },
        ],
      });
      sinon.stub(Project, 'findOne').resolves(project);
      const updated = await ProjectService.removeMember(
        ORG_ID,
        OWNER_ID,
        project._id.toString(),
        MEMBER_ID,
      );
      expect(updated.members).to.have.lengthOf(0);
    });
  });

  describe('findProjectsWithLinkedKbForUser', () => {
    it('returns projects where the user is a member and a linked KB exists', async () => {
      const linkedProject = makeProjectDoc({ linkedKnowledgeBaseId: 'kb-1' });
      const findStub = sinon.stub(Project, 'find').resolves([linkedProject] as any);
      const result = await ProjectService.findProjectsWithLinkedKbForUser(ORG_ID, MEMBER_ID);
      expect(result).to.deep.equal([linkedProject]);
      const findFilter = findStub.firstCall.args[0] as any;
      expect(findFilter.orgId.toString()).to.equal(ORG_ID);
      expect(findFilter.linkedKnowledgeBaseId).to.deep.equal({ $ne: null });
    });
  });

  describe('removeUserFromAllProjects', () => {
    it('pulls the user from every project member list in the org', async () => {
      const updateManyStub = sinon.stub(Project, 'updateMany').resolves({} as any);
      await ProjectService.removeUserFromAllProjects(ORG_ID, MEMBER_ID);
      expect(updateManyStub.calledOnce).to.equal(true);
      const [filter, update] = updateManyStub.firstCall.args;
      expect((filter as any).orgId.toString()).to.equal(ORG_ID);
      expect((update as any).$pull.members.principalId.toString()).to.equal(MEMBER_ID);
    });

    it('propagates DB failures so the caller can abort and retry', async () => {
      sinon.stub(Project, 'updateMany').rejects(new Error('db down'));
      try {
        await ProjectService.removeUserFromAllProjects(ORG_ID, MEMBER_ID);
        expect.fail('Expected rejection');
      } catch (error: any) {
        expect(error.message).to.equal('db down');
      }
    });
  });

  describe('getAccessibleProjectIds', () => {
    it('returns ids for owner, member, and org-visible projects', async () => {
      const ids = [new mongoose.Types.ObjectId(), new mongoose.Types.ObjectId()];
      const chain: any = {
        lean: sinon.stub().resolves(ids.map((id) => ({ _id: id }))),
      };
      const findStub = sinon.stub(Project, 'find').returns(chain);
      const result = await ProjectService.getAccessibleProjectIds(ORG_ID, OWNER_ID);
      expect(result).to.deep.equal(ids);
      const filter = findStub.firstCall.args[0] as any;
      expect(filter.$or).to.have.lengthOf(3);
    });
  });

  describe('touchActivity', () => {
    it('bumps lastActivityAt', async () => {
      const updateOneStub = sinon.stub(Project, 'updateOne').resolves({} as any);
      const projectId = new mongoose.Types.ObjectId().toString();
      await ProjectService.touchActivity(projectId);
      expect(updateOneStub.calledOnce).to.equal(true);
    });

    it('never throws when the update fails (best-effort)', async () => {
      sinon.stub(Project, 'updateOne').rejects(new Error('db down'));
      await ProjectService.touchActivity(new mongoose.Types.ObjectId().toString());
    });

    it('filters by a real ObjectId instance, not the raw caller-supplied string (NoSQL operator-injection guard)', async () => {
      const updateOneStub = sinon.stub(Project, 'updateOne').resolves({} as any);
      const projectId = new mongoose.Types.ObjectId().toString();
      await ProjectService.touchActivity(projectId);
      const filter = updateOneStub.firstCall.args[0] as { _id: unknown };
      expect(filter._id).to.be.instanceOf(mongoose.Types.ObjectId);
      expect((filter._id as mongoose.Types.ObjectId).toString()).to.equal(projectId);
    });

    it('skips the query and never throws for a non-ObjectId projectId', async () => {
      const updateOneStub = sinon.stub(Project, 'updateOne').resolves({} as any);
      await ProjectService.touchActivity('not-a-valid-object-id');
      expect(updateOneStub.called).to.equal(false);
    });

    it('skips the query for a query-object payload masquerading as projectId (operator injection attempt)', async () => {
      const updateOneStub = sinon.stub(Project, 'updateOne').resolves({} as any);
      // TS types this as `string`, but nothing stops a caller passing an
      // object at runtime — this is exactly the shape CodeQL's
      // "database query built from user-controlled sources" flags.
      await ProjectService.touchActivity({ $ne: null } as unknown as string);
      expect(updateOneStub.called).to.equal(false);
    });
  });
});
