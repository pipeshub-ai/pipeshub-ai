import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { AclVersionMigration } from '../../../../../src/modules/configuration_manager/services/migrations/acl_version.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { Project } from '../../../../../src/modules/projects/schema/project.schema';

describe('AclVersionMigration', () => {
  const logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
  let kv: Record<string, string>;
  let kvStore: any;
  beforeEach(() => {
    kv = {};
    kvStore = {
      get: sinon.stub().callsFake((p: string) => Promise.resolve(kv[p] ?? null)),
      set: sinon.stub().callsFake((p: string, v: string) => {
        kv[p] = v;
        return Promise.resolve();
      }),
    };
  });
  afterEach(() => sinon.restore());

  it('stamps only documents lacking aclVersion and records counts', async () => {
    const chat = sinon.stub(ChatSession.collection, 'updateMany').resolves({ modifiedCount: 4 } as any);
    const proj = sinon.stub(Project.collection, 'updateMany').resolves({ modifiedCount: 2 } as any);
    const res = await new AclVersionMigration(logger as any, kvStore).run();
    expect(res).to.deep.equal({ chatSessions: 4, projects: 2, errored: 0 });
    expect(chat.firstCall.args).to.deep.equal([{ aclVersion: { $exists: false } }, { $set: { aclVersion: 0 } }]);
    expect(proj.calledOnce).to.equal(true);
    expect(JSON.parse(kv[configPaths.aclVersionMigration] as string)).to.deep.equal(res);
  });

  it('is skipped once the flag exists', async () => {
    kv[configPaths.aclVersionMigration] = '{}';
    const chat = sinon.stub(ChatSession.collection, 'updateMany').resolves({ modifiedCount: 0 } as any);
    await new AclVersionMigration(logger as any, kvStore).run();
    expect(chat.called).to.equal(false);
  });

  it('withholds the flag when a collection write fails', async () => {
    sinon.stub(ChatSession.collection, 'updateMany').rejects(new Error('x'));
    sinon.stub(Project.collection, 'updateMany').resolves({ modifiedCount: 1 } as any);
    const res = await new AclVersionMigration(logger as any, kvStore).run();
    expect(res.errored).to.equal(1);
    expect(kv[configPaths.aclVersionMigration]).to.equal(undefined);
  });
});
