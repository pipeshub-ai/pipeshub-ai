import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  enqueueProjectKbSyncForAllOrgs,
  startProjectKbDriftRepair,
} from '../../../../src/modules/projects/services/project-kb-drift.service';
import { ProjectKnowledgeBaseService } from '../../../../src/modules/projects/services/project-kb.service';
import { Project } from '../../../../src/modules/projects/schema/project.schema';

const logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() } as never;

function projectDoc(): { _id: mongoose.Types.ObjectId } {
  return { _id: new mongoose.Types.ObjectId() };
}

function findChain(pages: unknown[][]): sinon.SinonStub {
  const find = sinon.stub();
  pages.forEach((page, index) => {
    find.onCall(index).returns({ sort: () => ({ limit: () => Promise.resolve(page) }) });
  });
  return find;
}

/** Stubs the per-project re-read to return the page doc itself unless `fresh` overrides it. */
function stubReRead(fresh: Map<string, unknown> = new Map()): sinon.SinonStub {
  return sinon.stub(Project, 'findOne').callsFake(((filter: { _id: mongoose.Types.ObjectId }) =>
    Promise.resolve(fresh.has(filter._id.toString()) ? fresh.get(filter._id.toString()) : filter)) as never);
}

describe('project KB drift repair', () => {
  afterEach(() => sinon.restore());

  it('queues a sync for each linked project, one org at a time, scoped to that org', async () => {
    const orgA = new mongoose.Types.ObjectId();
    const orgB = new mongoose.Types.ObjectId();
    sinon.stub(Project, 'distinct').resolves([orgA, orgB] as never);
    const a = projectDoc();
    const b = projectDoc();
    const find = findChain([[a], [b]]);
    sinon.stub(Project, 'find').callsFake(find as never);
    stubReRead(new Map([[a._id.toString(), a], [b._id.toString(), b]]));
    const enqueue = sinon.stub(ProjectKnowledgeBaseService, 'enqueueSync').resolves();

    const queued = await enqueueProjectKbSyncForAllOrgs(logger);

    expect(queued).to.equal(2);
    expect(find.firstCall.args[0]).to.deep.include({ orgId: orgA, isDeleted: false });
    expect(find.firstCall.args[0].linkedKnowledgeBaseId).to.deep.equal({ $ne: null });
    expect(find.secondCall.args[0]).to.deep.include({ orgId: orgB });
    expect(enqueue.args.map((args) => args[0])).to.deep.equal([a, b]);
  });

  it('pages through an org with an _id cursor until a short page', async () => {
    const orgId = new mongoose.Types.ObjectId();
    sinon.stub(Project, 'distinct').resolves([orgId] as never);
    const fullPage = Array.from({ length: 200 }, projectDoc);
    const lastOfFirst = fullPage[199];
    const find = findChain([fullPage, [projectDoc()]]);
    sinon.stub(Project, 'find').callsFake(find as never);
    stubReRead();
    const enqueue = sinon.stub(ProjectKnowledgeBaseService, 'enqueueSync').resolves();

    const queued = await enqueueProjectKbSyncForAllOrgs(logger);

    expect(queued).to.equal(201);
    expect(find.firstCall.args[0]).to.not.have.property('_id');
    expect(find.secondCall.args[0]._id).to.deep.equal({ $gt: lastOfFirst._id });
    expect(enqueue.callCount).to.equal(201);
  });

  it('keeps going when one project fails to queue', async () => {
    sinon.stub(Project, 'distinct').resolves([new mongoose.Types.ObjectId()] as never);
    sinon.stub(Project, 'find').callsFake(findChain([[projectDoc(), projectDoc()]]) as never);
    stubReRead();
    const enqueue = sinon.stub(ProjectKnowledgeBaseService, 'enqueueSync');
    enqueue.onFirstCall().rejects(new Error('mongo hiccup'));
    enqueue.onSecondCall().resolves();

    expect(await enqueueProjectKbSyncForAllOrgs(logger)).to.equal(1);
  });

  it('queues the project as re-read by id, not the page snapshot, and skips one that went away', async () => {
    sinon.stub(Project, 'distinct').resolves([new mongoose.Types.ObjectId()] as never);
    const kept = projectDoc();
    const gone = projectDoc();
    const find = findChain([[kept, gone]]);
    sinon.stub(Project, 'find').callsFake(find as never);
    const fresh = { _id: kept._id, updatedAt: new Date() };
    const reRead = stubReRead(new Map<string, unknown>([[kept._id.toString(), fresh], [gone._id.toString(), null]]));
    const enqueue = sinon.stub(ProjectKnowledgeBaseService, 'enqueueSync').resolves();

    expect(await enqueueProjectKbSyncForAllOrgs(logger)).to.equal(1);
    expect(find.firstCall.args[1]).to.deep.equal({ _id: 1 });
    expect(reRead.firstCall.args[0]).to.deep.include({ _id: kept._id, isDeleted: false });
    expect(enqueue.args.map((args) => args[0])).to.deep.equal([fresh]);
  });

  it('runs after the interval plus jitter, repeats, and stops on request', async () => {
    const clock = sinon.useFakeTimers();
    sinon.stub(Math, 'random').returns(0.5);
    const distinct = sinon.stub(Project, 'distinct').resolves([] as never);

    const handle = startProjectKbDriftRepair(logger, 1000, 400);
    await clock.tickAsync(1199);
    expect(distinct.callCount).to.equal(0);
    await clock.tickAsync(2);
    expect(distinct.callCount).to.equal(1);
    await clock.tickAsync(1200);
    expect(distinct.callCount).to.equal(2);

    handle.stop();
    await clock.tickAsync(5000);
    expect(distinct.callCount).to.equal(2);
    clock.restore();
  });
});
