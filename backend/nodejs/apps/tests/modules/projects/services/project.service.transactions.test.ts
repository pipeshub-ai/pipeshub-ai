import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { Project } from '../../../../src/modules/projects/schema/project.schema';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';

const SERVICE = '../../../../src/modules/projects/services/project.service';

const ORG_ID = new mongoose.Types.ObjectId().toString();
const OWNER_ID = new mongoose.Types.ObjectId().toString();
const MEMBER_ID = new mongoose.Types.ObjectId().toString();

function makeProjectDoc(overrides: Record<string, any> = {}): any {
  const doc: any = {
    _id: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(ORG_ID),
    userId: new mongoose.Types.ObjectId(OWNER_ID),
    members: [],
    visibility: 'private',
    isDeleted: false,
    ...overrides,
  };
  doc.save = sinon.stub().resolvesThis();
  return doc;
}

function makeSession() {
  return {
    startTransaction: sinon.stub(),
    commitTransaction: sinon.stub().resolves(),
    abortTransaction: sinon.stub().resolves(),
    endSession: sinon.stub().resolves(),
  };
}

describe('ProjectService.softDelete on a replica set', () => {
  // The service reads REPLICA_SET_AVAILABLE when it loads, so load a copy with it set.
  let ProjectService: typeof import('../../../../src/modules/projects/services/project.service').ProjectService;
  const previousEnv = process.env.REPLICA_SET_AVAILABLE;
  const resolved = require.resolve(SERVICE);
  const previousModule = require.cache[resolved];

  before(() => {
    process.env.REPLICA_SET_AVAILABLE = 'true';
    delete require.cache[resolved];
    ProjectService = require(SERVICE).ProjectService;
  });

  after(() => {
    if (previousEnv === undefined) {
      delete process.env.REPLICA_SET_AVAILABLE;
    } else {
      process.env.REPLICA_SET_AVAILABLE = previousEnv;
    }
    // Put the original copy back rather than just evicting ours: modules that
    // already imported it keep that reference, and a later test file stubbing
    // a freshly loaded third copy would not reach them.
    if (previousModule) {
      require.cache[resolved] = previousModule;
    } else {
      delete require.cache[resolved];
    }
  });

  afterEach(() => sinon.restore());

  it('unlinks sessions and marks the project deleted inside one committed transaction', async () => {
    const project = makeProjectDoc();
    const session = makeSession();
    sinon.stub(Project, 'findOne').resolves(project);
    sinon.stub(mongoose, 'startSession').resolves(session as any);
    const updateManyStub = sinon.stub(ChatSession, 'updateMany').resolves({} as any);

    await ProjectService.softDelete(ORG_ID, OWNER_ID, project._id.toString());

    expect(updateManyStub.firstCall.args[2]).to.deep.equal({ session });
    expect(project.save.firstCall.args[0]).to.deep.equal({ session });
    expect(project.isDeleted).to.equal(true);
    expect(project.deletedBy.toString()).to.equal(OWNER_ID);
    sinon.assert.callOrder(
      session.startTransaction,
      updateManyStub,
      project.save,
      session.commitTransaction,
      session.endSession,
    );
    expect(session.abortTransaction.called).to.equal(false);
  });

  it('aborts, rethrows the original error, and still ends the session when the save fails', async () => {
    const project = makeProjectDoc();
    const saveError = new Error('write conflict');
    project.save = sinon.stub().rejects(saveError);
    const session = makeSession();
    sinon.stub(Project, 'findOne').resolves(project);
    sinon.stub(mongoose, 'startSession').resolves(session as any);
    sinon.stub(ChatSession, 'updateMany').resolves({} as any);

    let caught: unknown;
    try {
      await ProjectService.softDelete(ORG_ID, OWNER_ID, project._id.toString());
    } catch (error) {
      caught = error;
    }

    expect(caught).to.equal(saveError);
    expect(session.abortTransaction.calledOnce).to.equal(true);
    expect(session.commitTransaction.called).to.equal(false);
    expect(session.endSession.calledOnce).to.equal(true);
  });

  it('aborts without touching the project when unlinking sessions fails', async () => {
    const project = makeProjectDoc();
    const unlinkError = new Error('updateMany timed out');
    const session = makeSession();
    sinon.stub(Project, 'findOne').resolves(project);
    sinon.stub(mongoose, 'startSession').resolves(session as any);
    sinon.stub(ChatSession, 'updateMany').rejects(unlinkError);

    let caught: unknown;
    try {
      await ProjectService.softDelete(ORG_ID, OWNER_ID, project._id.toString());
    } catch (error) {
      caught = error;
    }

    expect(caught).to.equal(unlinkError);
    expect(project.save.called).to.equal(false);
    expect(project.isDeleted).to.equal(false);
    expect(session.abortTransaction.calledOnce).to.equal(true);
    expect(session.endSession.calledOnce).to.equal(true);
  });

  it('opens no session for a repeated delete of an already-deleted project', async () => {
    const project = makeProjectDoc({ isDeleted: true });
    sinon.stub(Project, 'findOne').resolves(project);
    const startSessionStub = sinon.stub(mongoose, 'startSession');

    await ProjectService.softDelete(ORG_ID, OWNER_ID, project._id.toString());

    expect(startSessionStub.called).to.equal(false);
  });

  it('opens no session when the caller is not the owner', async () => {
    const project = makeProjectDoc({
      members: [
        { principalType: 'user', principalId: new mongoose.Types.ObjectId(MEMBER_ID), role: 'editor' },
      ],
    });
    sinon.stub(Project, 'findOne').resolves(project);
    const startSessionStub = sinon.stub(mongoose, 'startSession');

    let caught: unknown;
    try {
      await ProjectService.softDelete(ORG_ID, MEMBER_ID, project._id.toString());
    } catch (error) {
      caught = error;
    }

    // The fresh module copy is still wired to the shared error classes.
    expect(caught).to.be.instanceOf(ForbiddenError);
    expect(startSessionStub.called).to.equal(false);
  });
});
