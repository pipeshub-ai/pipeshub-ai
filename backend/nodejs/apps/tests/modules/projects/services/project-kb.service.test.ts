import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ProjectKnowledgeBaseService } from '../../../../src/modules/projects/services/project-kb.service';
import { Project } from '../../../../src/modules/projects/schema/project.schema';
import { OutboxEvent } from '../../../../src/libs/services/outbox/outbox.schema';
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
  let create: sinon.SinonStub;

  beforeEach(() => {
    exec = sinon.stub(connectorUtils, 'executeConnectorCommand');
    create = sinon.stub(OutboxEvent, 'create').resolves([] as never);
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

    it('queues a sync so the project owner ends up owning a KB someone else created', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject());
      sinon.stub(Project, 'findOneAndUpdate').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-new' }));
      exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(200, { id: 'kb-new' }));

      await ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID);

      expect(exec.getCalls().map((call) => call.args[0])).to.not.include(permissionsUrl('kb-new'));
      const payload = JSON.parse(create.firstCall.args[0][0].value).payload;
      expect(payload).to.include({ kbId: 'kb-new', ownerUserId: OWNER_ID });
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

    it('propagates a failed sync enqueue instead of reporting the KB as ready', async () => {
      sinon.stub(Project, 'findOne').resolves(makeProject());
      sinon.stub(Project, 'findOneAndUpdate').resolves(makeProject({ linkedKnowledgeBaseId: 'kb-new' }));
      exec.withArgs(`${KB_URL}/`, HttpMethod.POST).resolves(respond(201, { id: 'kb-new' }));
      create.rejects(new Error('mongo down'));

      await expectRejection(
        ProjectKnowledgeBaseService.ensureLinkedKb(appConfig, HEADERS, ORG_ID, PROJECT_ID),
        Error,
        /mongo down/,
      );
    });
  });

  describe('enqueueSync', () => {
    function queuedRows(): any[] {
      return create.getCalls().map((call) => call.args[0][0]);
    }

    it('is a no-op when the project has no linked KB', async () => {
      await ProjectKnowledgeBaseService.enqueueSync(makeProject());

      expect(create.called).to.equal(false);
    });

    it('writes one pending projectKbSync row ordered by project, with no HTTP call', async () => {
      await ProjectKnowledgeBaseService.enqueueSync(makeProject({ linkedKnowledgeBaseId: 'kb-1' }));

      expect(exec.called).to.equal(false);
      const [row] = queuedRows();
      expect(row).to.include({
        topic: 'entity-events',
        key: 'projectKbSync',
        orderingKey: `project:${PROJECT_ID}`,
        status: 'pending',
        attempts: 0,
      });
      const event = JSON.parse(row.value);
      expect(event.eventType).to.equal('projectKbSync');
      expect(event.timestamp).to.be.a('number');
      expect(event.payload).to.deep.equal({
        orgId: ORG_ID,
        projectId: PROJECT_ID,
        kbId: 'kb-1',
        ownerUserId: OWNER_ID,
        editorUserIds: [],
        viewerUserIds: [],
        teams: [],
        orgVisible: false,
      });
    });

    it('maps editors to WRITER and viewers to READER through the role table, teams carrying a role', async () => {
      const editor = userMember('editor');
      const viewer = userMember('viewer');
      const editorTeam = teamMember('editor');
      const viewerTeam = teamMember('viewer');

      await ProjectKnowledgeBaseService.enqueueSync(
        makeProject({
          linkedKnowledgeBaseId: 'kb-1',
          visibility: 'org',
          members: [editor.member, viewer.member, editorTeam.member, viewerTeam.member],
        }),
      );

      const { payload } = JSON.parse(queuedRows()[0].value);
      expect(payload.editorUserIds).to.deep.equal([editor.id]);
      expect(payload.viewerUserIds).to.deep.equal([viewer.id]);
      expect(payload.teams).to.deep.equal([
        { teamId: editorTeam.id, role: 'WRITER' },
        { teamId: viewerTeam.id, role: 'READER' },
      ]);
      expect(payload.orgVisible).to.equal(true);
    });

    it('lists a removed member nowhere, and never lists the owner as an editor or viewer', async () => {
      const removed = userMember('viewer');
      const ownerRow = {
        principalType: 'user',
        principalId: new mongoose.Types.ObjectId(OWNER_ID),
        role: 'editor',
      };
      const project = makeProject({ linkedKnowledgeBaseId: 'kb-1', members: [removed.member, ownerRow] });

      await ProjectKnowledgeBaseService.enqueueSync(project, undefined, removed.id);

      const { payload } = JSON.parse(queuedRows()[0].value);
      expect(payload.viewerUserIds).to.deep.equal([]);
      expect(payload.editorUserIds).to.deep.equal([]);
    });

    it('writes the row inside the caller session when one is given, and without one otherwise', async () => {
      const session = { id: 'session-1' } as unknown as mongoose.ClientSession;
      const project = makeProject({ linkedKnowledgeBaseId: 'kb-1' });

      await ProjectKnowledgeBaseService.enqueueSync(project, session);
      await ProjectKnowledgeBaseService.enqueueSync(project);

      expect(create.firstCall.args[1]).to.deep.equal({ session });
      expect(create.secondCall.args[1]).to.deep.equal({});
    });

    it('propagates a failed outbox write so the caller does not report success', async () => {
      create.rejects(new Error('mongo down'));

      await expectRejection(
        ProjectKnowledgeBaseService.enqueueSync(makeProject({ linkedKnowledgeBaseId: 'kb-1' })),
        Error,
        /mongo down/,
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
