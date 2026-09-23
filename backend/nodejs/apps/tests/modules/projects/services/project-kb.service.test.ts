import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ProjectKnowledgeBaseService } from '../../../../src/modules/projects/services/project-kb.service';
import { Project } from '../../../../src/modules/projects/schema/project.schema';
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils';
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum';
import {
  ForbiddenError,
  InternalServerError,
  NotFoundError,
} from '../../../../src/libs/errors/http.errors';

/** Awaits `promise`, asserts it rejects with an instance of `ErrorType`
 * (optionally matching `messagePattern`), and returns the error. */
async function expectRejection(
  promise: Promise<unknown>,
  ErrorType: new (...args: any[]) => Error,
  messagePattern?: RegExp,
): Promise<Error> {
  let caught: Error | undefined;
  try {
    await promise;
  } catch (error: any) {
    caught = error;
  }
  if (!caught) {
    expect.fail('Expected promise to reject');
  }
  expect(caught).to.be.instanceOf(ErrorType);
  if (messagePattern) {
    expect(caught.message).to.match(messagePattern);
  }
  return caught;
}

const ORG_ID = new mongoose.Types.ObjectId().toString();
const OTHER_ORG_ID = new mongoose.Types.ObjectId().toString();
const OWNER_ID = new mongoose.Types.ObjectId().toString();
const PROJECT_ID = new mongoose.Types.ObjectId().toString();

const CONNECTOR_BACKEND = 'http://localhost:8088';
const KB_URL = `${CONNECTOR_BACKEND}/api/v1/kb`;
const appConfig = { connectorBackend: CONNECTOR_BACKEND } as any;
const HEADERS = { authorization: 'Bearer test-token' };

function permissionsUrl(kbId: string): string {
  return `${KB_URL}/${kbId}/permissions`;
}

function respond(statusCode: number, data?: unknown): any {
  return { statusCode, data };
}

function makeProject(overrides: Record<string, any> = {}): any {
  return {
    _id: new mongoose.Types.ObjectId(PROJECT_ID),
    orgId: new mongoose.Types.ObjectId(ORG_ID),
    userId: new mongoose.Types.ObjectId(OWNER_ID),
    linkedKnowledgeBaseId: null,
    members: [],
    visibility: 'private',
    ...overrides,
  };
}

function userMember(role: 'editor' | 'viewer'): { member: any; id: string } {
  const id = new mongoose.Types.ObjectId().toString();
  return {
    id,
    member: { principalType: 'user', principalId: new mongoose.Types.ObjectId(id), role },
  };
}

function teamMember(role: 'editor' | 'viewer'): { member: any; id: string } {
  const id = new mongoose.Types.ObjectId().toString();
  return {
    id,
    member: { principalType: 'team', principalId: new mongoose.Types.ObjectId(id), role },
  };
}

/** Bodies of every call the stub received for `method uri`, in call order. */
function bodiesSentTo(exec: sinon.SinonStub, uri: string, method: HttpMethod): any[] {
  return exec
    .getCalls()
    .filter((call) => call.args[0] === uri && call.args[1] === method)
    .map((call) => call.args[3]);
}

describe('ProjectKnowledgeBaseService', () => {
  // Unmatched calls resolve `undefined`, so a request the test did not expect
  // fails loudly on `response.statusCode` instead of passing silently.
  let exec: sinon.SinonStub;

  beforeEach(() => {
    exec = sinon.stub(connectorUtils, 'executeConnectorCommand');
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('ensureLinkedKb', () => {
    it('throws NotFoundError when the project does not exist, without calling the KB service', async () => {
      const findOneStub = sinon.stub(Project, 'findOne').resolves(null);

      await expectRejection(
        ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
        NotFoundError,
        /Project not found/,
      );

      expect(findOneStub.firstCall.args[0]).to.deep.equal({ _id: PROJECT_ID, isDeleted: false });
      expect(exec.called).to.equal(false);
    });

    it('throws NotFoundError for a project in another org, without calling the KB service', async () => {
      sinon
        .stub(Project, 'findOne')
        .resolves(makeProject({ orgId: new mongoose.Types.ObjectId(OTHER_ORG_ID), linkedKnowledgeBaseId: 'kb-1' }));
      const linkStub = sinon.stub(Project, 'findOneAndUpdate');

      await expectRejection(
        ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
        NotFoundError,
      );

      expect(exec.called).to.equal(false);
      expect(linkStub.called).to.equal(false);
    });

    it('returns the already-linked KB id when it still exists upstream, without creating another', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-1' }));
      const linkStub = sinon.stub(Project, 'findOneAndUpdate');
      exec.withArgs(`${KB_URL}/kb-1`, HttpMethod.GET).resolves(respond(200, { id: 'kb-1' }));

      const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

      expect(kbId).to.equal('kb-1');
      expect(exec.calledOnce).to.equal(true);
      expect(exec.firstCall.args[2]).to.equal(HEADERS);
      expect(linkStub.called).to.equal(false);
    });

    it('keeps the linked KB id when the existence check fails with anything other than 404', async () => {
      // Only a 404 proves the KB is gone; recreating on a transient 5xx would
      // orphan the real KB and every file already indexed into it.
      sinon.stub(Project, 'findOne').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-1' }));
      const linkStub = sinon.stub(Project, 'findOneAndUpdate');
      exec.withArgs(`${KB_URL}/kb-1`, HttpMethod.GET).resolves(respond(503));

      const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

      expect(kbId).to.equal('kb-1');
      expect(bodiesSentTo(exec, `${KB_URL}/`, HttpMethod.POST)).to.have.length(0);
      expect(linkStub.called).to.equal(false);
    });

    it('creates a hidden KB named after the project and links it when none is linked yet', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject());
      const linkStub = sinon
        .stub(Project, 'findOneAndUpdate')
        .resolves(makeProject({ linkedKnowledgeBaseId: 'kb-new' }));
      exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(201, { id: 'kb-new' }));
      exec.withArgs(permissionsUrl('kb-new'), HttpMethod.POST).resolves(respond(200));

      const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

      expect(kbId).to.equal('kb-new');
      expect(exec.getCalls().some((call) => call.args[1] === HttpMethod.GET)).to.equal(false);
      expect(bodiesSentTo(exec, `${KB_URL}/`, HttpMethod.POST)).to.deep.equal([
        { name: `project:${PROJECT_ID}`, isHidden: true },
      ]);
      expect(linkStub.firstCall.args).to.deep.equal([
        { _id: PROJECT_ID, linkedKnowledgeBaseId: null },
        { $set: { linkedKnowledgeBaseId: 'kb-new' } },
        { new: true },
      ]);
    });

    it('grants the project owner on the new KB even when someone else created it', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject());
      sinon.stub(Project, 'findOneAndUpdate').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-new' }));
      exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(200, { id: 'kb-new' }));
      exec.withArgs(permissionsUrl('kb-new'), HttpMethod.POST).resolves(respond(200));

      await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

      expect(bodiesSentTo(exec, permissionsUrl('kb-new'), HttpMethod.POST)).to.deep.equal([
        { userIds: [OWNER_ID], teamIds: [], role: 'OWNER' },
      ]);
    });

    it('self-heals a stale link: recreates on 404 and guards the swap on the stale id', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-gone' }));
      const linkStub = sinon
        .stub(Project, 'findOneAndUpdate')
        .resolves(makeProject({ linkedKnowledgeBaseId: 'kb-new' }));
      exec.withArgs(`${KB_URL}/kb-gone`, HttpMethod.GET).resolves(respond(404));
      exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(201, { id: 'kb-new' }));
      exec.withArgs(permissionsUrl('kb-new'), HttpMethod.POST).resolves(respond(200));

      const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

      expect(kbId).to.equal('kb-new');
      // A concurrent healer that already swapped the stale id must win, so the
      // filter compares against the value read above rather than against null.
      expect(linkStub.firstCall.args[0]).to.deep.equal({ _id: PROJECT_ID, linkedKnowledgeBaseId: 'kb-gone' });
    });

    const creationFailures: Array<{ status: number; ErrorType: new (...args: any[]) => Error }> = [
      { status: 403, ErrorType: ForbiddenError },
      { status: 500, ErrorType: InternalServerError },
    ];
    for (const { status, ErrorType } of creationFailures) {
      it(`maps a ${status} from KB creation to ${ErrorType.name} and leaves Mongo untouched`, async () => {
        sinon.stub(Project, 'findOne').resolves(makeProject());
        const linkStub = sinon.stub(Project, 'findOneAndUpdate');
        exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(status, { detail: 'nope' }));

        await expectRejection(
          ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
          ErrorType,
        );

        expect(linkStub.called).to.equal(false);
      });
    }

    for (const [label, data] of [
      ['no body', undefined],
      ['a body without an id', { name: 'project:x' }],
    ] as const) {
      it(`throws InternalServerError when creation succeeds with ${label}`, async () => {
        sinon.stub(Project, 'findOne').resolves(makeProject());
        const linkStub = sinon.stub(Project, 'findOneAndUpdate');
        exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(201, data));

        await expectRejection(
          ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
          InternalServerError,
          /did not return an id/,
        );

        expect(linkStub.called).to.equal(false);
      });
    }

    describe('when a concurrent caller links a KB first', () => {
      function stubLostRace(winner: any): void {
        sinon
          .stub(Project, 'findOne')
          .onFirstCall()
          .resolves(makeProject())
          .onSecondCall()
          .resolves(winner);
        sinon.stub(Project, 'findOneAndUpdate').resolves(null);
        exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(201, { id: 'kb-orphan' }));
      }

      it('deletes its orphaned KB and returns the winner\'s id without granting anything', async () => {
        stubLostRace(makeProject({ linkedKnowledgeBaseId: 'kb-winner' }));
        exec.withArgs(`${KB_URL}/kb-orphan`, HttpMethod.DELETE).resolves(respond(200));

        const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

        expect(kbId).to.equal('kb-winner');
        expect(exec.calledWith(`${KB_URL}/kb-orphan`, HttpMethod.DELETE, HEADERS)).to.equal(true);
        expect(exec.getCalls().some((call) => String(call.args[0]).endsWith('/permissions'))).to.equal(false);
      });

      it('still returns the winner\'s id when deleting the orphan fails', async () => {
        stubLostRace(makeProject({ linkedKnowledgeBaseId: 'kb-winner' }));
        exec.withArgs(`${KB_URL}/kb-orphan`, HttpMethod.DELETE).rejects(new Error('socket hang up'));

        const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

        expect(kbId).to.equal('kb-winner');
      });

      for (const [label, winner] of [
        ['the project vanished', null],
        ['the winner holds no link', makeProject({ linkedKnowledgeBaseId: null })],
      ] as const) {
        it(`throws InternalServerError when ${label} on re-read`, async () => {
          stubLostRace(winner);
          exec.withArgs(`${KB_URL}/kb-orphan`, HttpMethod.DELETE).resolves(respond(200));

          await expectRejection(
            ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
            InternalServerError,
            /Failed to link project knowledge base/,
          );
        });
      }
    });

    it('propagates a permission-sync failure instead of reporting the KB as ready', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject());
      sinon.stub(Project, 'findOneAndUpdate').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-new' }));
      exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(201, { id: 'kb-new' }));
      exec.withArgs(permissionsUrl('kb-new'), HttpMethod.POST).resolves(respond(500));

      await expectRejection(
        ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
        InternalServerError,
      );
    });
  });

  describe('syncMemberPermissions', () => {
    it('is a no-op when the project has no linked KB', async () => {
      await ProjectKnowledgeBaseService.syncMemberPermissions(appConfig, HEADERS, makeProject());

      expect(exec.called).to.equal(false);
    });

    it('grants only the owner, as OWNER, for a private project with no members', async () => {
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.syncMemberPermissions(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
      );

      expect(exec.calledOnce).to.equal(true);
      expect(exec.firstCall.args).to.deep.equal([
        permissionsUrl('kb-1'),
        HttpMethod.POST,
        HEADERS,
        { userIds: [OWNER_ID], teamIds: [], role: 'OWNER' },
      ]);
    });

    it('maps editors to WRITER, viewers to READER, and sends teams without a role', async () => {
      const editorA = userMember('editor');
      const editorB = userMember('editor');
      const viewer = userMember('viewer');
      const editorTeam = teamMember('editor');
      const viewerTeam = teamMember('viewer');
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.syncMemberPermissions(
        appConfig,
        HEADERS,
        makeProject({
          linkedKnowledgeBaseId: 'kb-1',
          members: [editorA.member, viewer.member, editorTeam.member, editorB.member, viewerTeam.member],
        }),
      );

      const bodies = bodiesSentTo(exec, permissionsUrl('kb-1'), HttpMethod.POST);
      expect(bodies).to.deep.equal([
        { userIds: [OWNER_ID], teamIds: [], role: 'OWNER' },
        { userIds: [editorA.id, editorB.id], teamIds: [], role: 'WRITER' },
        { userIds: [viewer.id], teamIds: [], role: 'READER' },
        { userIds: [], teamIds: [editorTeam.id, viewerTeam.id] },
      ]);
      // The KB API rejects a team grant that carries a role.
      expect(bodies[3]).to.not.have.property('role');
    });

    it('sends no request for an empty bucket', async () => {
      const viewer = userMember('viewer');
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.syncMemberPermissions(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1', members: [viewer.member] }),
      );

      expect(bodiesSentTo(exec, permissionsUrl('kb-1'), HttpMethod.POST)).to.deep.equal([
        { userIds: [OWNER_ID], teamIds: [], role: 'OWNER' },
        { userIds: [viewer.id], teamIds: [], role: 'READER' },
      ]);
    });

    it('grants the synthetic all-org team when visibility is "org"', async () => {
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.syncMemberPermissions(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1', visibility: 'org' }),
      );

      expect(bodiesSentTo(exec, permissionsUrl('kb-1'), HttpMethod.POST)).to.deep.equal([
        { userIds: [OWNER_ID], teamIds: [], role: 'OWNER' },
        { userIds: [], teamIds: [`all_${ORG_ID}`] },
      ]);
    });

    it('never grants the all-org team for a private project, even one with team members', async () => {
      const team = teamMember('viewer');
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.syncMemberPermissions(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1', members: [team.member] }),
      );

      const grantedTeamIds = bodiesSentTo(exec, permissionsUrl('kb-1'), HttpMethod.POST).flatMap(
        (body) => body.teamIds,
      );
      expect(grantedTeamIds).to.deep.equal([team.id]);
    });

    it('treats 201 from the permissions endpoint as success', async () => {
      exec.resolves(respond(201));

      await ProjectKnowledgeBaseService.syncMemberPermissions(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1', visibility: 'org' }),
      );

      expect(exec.callCount).to.equal(2);
    });

    it('surfaces a failed user grant and attempts no further grants', async () => {
      const viewer = userMember('viewer');
      exec.resolves(respond(403, { detail: 'Not a KB owner' }));

      await expectRejection(
        ProjectKnowledgeBaseService.syncMemberPermissions(
          appConfig,
          HEADERS,
          makeProject({ linkedKnowledgeBaseId: 'kb-1', members: [viewer.member], visibility: 'org' }),
        ),
        ForbiddenError,
        /Not a KB owner/,
      );

      expect(exec.calledOnce).to.equal(true);
    });

    it('surfaces a failed team grant', async () => {
      const team = teamMember('editor');
      exec.onFirstCall().resolves(respond(200));
      exec.onSecondCall().resolves(respond(500));

      await expectRejection(
        ProjectKnowledgeBaseService.syncMemberPermissions(
          appConfig,
          HEADERS,
          makeProject({ linkedKnowledgeBaseId: 'kb-1', members: [team.member] }),
        ),
        InternalServerError,
      );
    });
  });

  describe('revokePrincipalPermission', () => {
    const principalId = new mongoose.Types.ObjectId().toString();

    it('is a no-op when the project has no linked KB', async () => {
      await ProjectKnowledgeBaseService.revokePrincipalPermission(
        appConfig,
        HEADERS,
        makeProject(),
        principalId,
        'user',
      );

      expect(exec.called).to.equal(false);
    });

    it('revokes a user by userIds', async () => {
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.revokePrincipalPermission(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
        principalId,
        'user',
      );

      expect(exec.calledOnce).to.equal(true);
      expect(exec.firstCall.args).to.deep.equal([
        permissionsUrl('kb-1'),
        HttpMethod.DELETE,
        HEADERS,
        { userIds: [principalId], teamIds: [] },
      ]);
    });

    it('revokes a team by teamIds', async () => {
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.revokePrincipalPermission(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
        principalId,
        'team',
      );

      expect(exec.firstCall.args[3]).to.deep.equal({ userIds: [], teamIds: [principalId] });
    });

    it('treats 404 (the principal never held a KB permission) as success', async () => {
      exec.resolves(respond(404));

      await ProjectKnowledgeBaseService.revokePrincipalPermission(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
        principalId,
        'user',
      );
    });

    it('surfaces any other failure so a stale permission edge is never left silently', async () => {
      exec.resolves(respond(500));

      await expectRejection(
        ProjectKnowledgeBaseService.revokePrincipalPermission(
          appConfig,
          HEADERS,
          makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
          principalId,
          'user',
        ),
        InternalServerError,
      );
    });
  });

  describe('revokeOrgVisibility', () => {
    it('is a no-op when the project has no linked KB', async () => {
      await ProjectKnowledgeBaseService.revokeOrgVisibility(appConfig, HEADERS, makeProject());

      expect(exec.called).to.equal(false);
    });

    it('revokes the synthetic all-org team for the project\'s own org', async () => {
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.revokeOrgVisibility(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
      );

      expect(exec.calledOnce).to.equal(true);
      expect(exec.firstCall.args).to.deep.equal([
        permissionsUrl('kb-1'),
        HttpMethod.DELETE,
        HEADERS,
        { userIds: [], teamIds: [`all_${ORG_ID}`] },
      ]);
    });

    it('treats 404 (the edge was never granted) as success', async () => {
      exec.resolves(respond(404));

      await ProjectKnowledgeBaseService.revokeOrgVisibility(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
      );
    });

    it('surfaces any other failure', async () => {
      exec.resolves(respond(403, { detail: 'Not a KB owner' }));

      await expectRejection(
        ProjectKnowledgeBaseService.revokeOrgVisibility(
          appConfig,
          HEADERS,
          makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
        ),
        ForbiddenError,
      );
    });
  });

  describe('deleteLinkedKb', () => {
    it('is a no-op when the project has no linked KB', async () => {
      await ProjectKnowledgeBaseService.deleteLinkedKb(appConfig, HEADERS, makeProject());

      expect(exec.called).to.equal(false);
    });

    it('deletes the linked KB with no request body', async () => {
      exec.resolves(respond(200));

      await ProjectKnowledgeBaseService.deleteLinkedKb(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
      );

      expect(exec.calledOnce).to.equal(true);
      expect(exec.firstCall.args).to.deep.equal([`${KB_URL}/kb-1`, HttpMethod.DELETE, HEADERS]);
    });

    it('treats 404 (already deleted) as success so a retried delete never fails here', async () => {
      exec.resolves(respond(404));

      await ProjectKnowledgeBaseService.deleteLinkedKb(
        appConfig,
        HEADERS,
        makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
      );
    });

    it('surfaces any other failure', async () => {
      exec.resolves(respond(500));

      await expectRejection(
        ProjectKnowledgeBaseService.deleteLinkedKb(
          appConfig,
          HEADERS,
          makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
        ),
        InternalServerError,
      );
    });

    it('propagates a transport error unchanged', async () => {
      const transportError = new Error('socket hang up');
      exec.rejects(transportError);

      const caught = await expectRejection(
        ProjectKnowledgeBaseService.deleteLinkedKb(
          appConfig,
          HEADERS,
          makeProject({ linkedKnowledgeBaseId: 'kb-1' }),
        ),
        Error,
      );

      expect(caught).to.equal(transportError);
    });
  });
});
