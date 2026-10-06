import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Types } from 'mongoose';
import { ProjectKnowledgeBaseService } from '../../../../src/modules/projects/services/project-kb.service';
import { enqueueProjectKbSyncForAllOrgs } from '../../../../src/modules/projects/services/project-kb-drift.service';
import { OutboxEvent } from '../../../../src/libs/services/outbox/outbox.schema';
import { Project } from '../../../../src/modules/projects/schema/project.schema';

const uri = process.env.PCC_MONGO_URI;
const logger = { info: () => undefined, warn: () => undefined, error: () => undefined, debug: () => undefined } as never;

;(uri ? describe : describe.skip)('project KB sync outbox against a real MongoDB replica set', function () {
  this.timeout(30_000);
  const orgId = new Types.ObjectId();
  const ownerId = new Types.ObjectId();

  const seedProject = async (overrides: Record<string, unknown> = {}) =>
    Project.create({
      orgId,
      userId: ownerId,
      name: `p-${new Types.ObjectId()}`,
      linkedKnowledgeBaseId: `kb-${new Types.ObjectId()}`,
      members: [],
      ...overrides,
    });

  const rowsFor = (projectId: Types.ObjectId) =>
    OutboxEvent.find({ key: 'projectKbSync', orderingKey: `project:${projectId}` }).lean();

  let replicaSet = false;

  before(async () => {
    await mongoose.connect(uri as string);
    const hello = (await mongoose.connection.db!.admin().command({ hello: 1 })) as { setName?: string };
    replicaSet = Boolean(hello.setName);
    await Project.init();
    await OutboxEvent.init();
  });
  after(async () => {
    await Project.deleteMany({ orgId });
    await OutboxEvent.deleteMany({ key: 'projectKbSync' });
    await mongoose.disconnect();
  });
  afterEach(async () => {
    await Project.deleteMany({ orgId });
    await OutboxEvent.deleteMany({ key: 'projectKbSync' });
  });

  it('commits the outbox row with the caller transaction', async function () {
    if (!replicaSet) this.skip();
    const project = await seedProject();
    const session = await mongoose.startSession();
    try {
      await session.withTransaction(async () => {
        await ProjectKnowledgeBaseService.enqueueSync(project, session);
        expect(await rowsFor(project._id as Types.ObjectId)).to.have.length(0);
      });
    } finally {
      await session.endSession();
    }

    const rows = await rowsFor(project._id as Types.ObjectId);
    expect(rows).to.have.length(1);
    expect(rows[0]).to.include({ topic: 'entity-events', status: 'pending' });
    expect(JSON.parse(rows[0].value).payload.kbId).to.equal(project.linkedKnowledgeBaseId);
  });

  it('leaves no outbox row when the caller transaction aborts', async function () {
    if (!replicaSet) this.skip();
    const project = await seedProject();
    const session = await mongoose.startSession();
    try {
      await session
        .withTransaction(async () => {
          await ProjectKnowledgeBaseService.enqueueSync(project, session);
          throw new Error('membership write failed');
        })
        .catch(() => undefined);
    } finally {
      await session.endSession();
    }

    expect(await rowsFor(project._id as Types.ObjectId)).to.have.length(0);
  });

  it('drift repair queues linked, live projects of every org and skips unlinked and deleted ones', async () => {
    const otherOrg = new Types.ObjectId();
    const linked = await seedProject();
    const linkedElsewhere = await seedProject({ orgId: otherOrg });
    const unlinked = await seedProject({ linkedKnowledgeBaseId: null });
    const deleted = await seedProject({ isDeleted: true });
    try {
      await enqueueProjectKbSyncForAllOrgs(logger);

      expect(await rowsFor(linked._id as Types.ObjectId)).to.have.length(1);
      const [other] = await rowsFor(linkedElsewhere._id as Types.ObjectId);
      expect(JSON.parse(other.value).payload.orgId).to.equal(otherOrg.toString());
      expect(await rowsFor(unlinked._id as Types.ObjectId)).to.have.length(0);
      expect(await rowsFor(deleted._id as Types.ObjectId)).to.have.length(0);
    } finally {
      await Project.deleteMany({ orgId: otherOrg });
    }
  });
});
