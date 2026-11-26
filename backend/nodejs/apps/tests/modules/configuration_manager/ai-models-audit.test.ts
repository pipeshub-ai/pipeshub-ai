/**
 * CPD-6 CPD6-13: an AI-model save is one audited control-plane write. It
 * lands as one `config.changed` naming the model types it touched, moves the
 * saving org's head and the deployment's revision (every org's pins read the
 * one document), and never carries an entry's configuration. A save whose
 * store write fails records nothing and moves nothing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../src/libs/encryptor/encryptor';
import { updateModelRoles } from '../../../src/modules/configuration_manager/controller/cm_controller';
import { configPaths } from '../../../src/modules/configuration_manager/paths/paths';
import {
  aiModelsOutline,
  auditedAiModelsSave,
  plainAiModelsSave,
} from '../../../src/modules/configuration_manager/utils/ai-models-save';
import { assertNoCanary, canary } from '../agent-harness/support/canary-scan';
import { auditedControlPlane } from '../control-plane/support/in-memory';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';

const ORG = 'org-models';
const SECRET = canary('api-key');
const MODELS = {
  llm: [
    { modelKey: 'mk-1', provider: 'openAI', isDefault: true, configuration: { model: 'gpt-x', apiKey: SECRET } },
    { modelKey: 'mk-2', provider: 'anthropic', configuration: { model: 'claude-x', apiKey: SECRET } },
  ],
  embedding: [],
};

class Store {
  values = new Map<string, string>();
  failing = false;

  async get<T>(key: string): Promise<T | null> {
    return (this.values.get(key) as T | undefined) ?? null;
  }

  async set(key: string, value: unknown): Promise<void> {
    if (this.failing) throw new Error('store down');
    this.values.set(key, value as unknown as string);
  }

  async compareAndSet<T>(key: string, expected: T | null, value: T): Promise<boolean> {
    if (this.failing) throw new Error('store down');
    if ((this.values.get(key) ?? null) !== expected) return false;
    this.values.set(key, value as unknown as string);
    return true;
  }
}

describe('CPD-6 AI-model saves are audited and move the heads pins are cached under (CPD6-13)', () => {
  let kv: Store;
  let audit: ReturnType<typeof auditedControlPlane>;
  let heads: InMemoryHeadRevisions;
  const by = { user: { userId: 'u-admin', orgId: ORG } };

  beforeEach(() => {
    kv = new Store();
    audit = auditedControlPlane();
    heads = new InMemoryHeadRevisions();
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'a'.repeat(64) } as never);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (value: string) => `encrypted:${value}`,
      decrypt: (value: string) => value.replace('encrypted:', ''),
    } as never);
  });
  afterEach(() => sinon.restore());

  const save = () => auditedAiModelsSave(kv as never, audit.transaction, heads);

  it('a save that lands: one entry naming the types it touched, the org head and the deployment revision moved', async () => {
    expect(await save()(by, null, 'encrypted:doc', MODELS, ['llm', 'embedding'])).to.equal(true);
    expect(kv.values.get(configPaths.aiModels)).to.equal('encrypted:doc');
    const entries = audit.configEntries(ORG);
    expect(entries).to.have.length(1);
    expect(entries[0]!.fields).to.include({
      scope: `org:${ORG}`,
      objectType: 'ai-models',
      objectId: 'aiModels',
      actor: 'user:u-admin',
      changed: 'embedding,llm',
    });
    expect(audit.transaction.heads.get(ORG)).to.equal(1);
    expect(heads.deployment).to.equal(1);
    expect(audit.outbox.events.map((event) => event.touched)).to.deep.equal([[{ kind: 'ai-models', id: 'aiModels' }]]);
  });

  it('neither the entry nor the change event carries an entry’s configuration', async () => {
    await save()(by, null, 'encrypted:doc', MODELS, ['llm']);
    assertNoCanary('the audit entries', audit.configEntries(ORG), { secrets: [SECRET] });
    assertNoCanary('the change events', audit.outbox.events, { secrets: [SECRET] });
    expect(aiModelsOutline(MODELS)).to.deep.equal({
      llm: [
        { modelKey: 'mk-1', provider: 'openAI', isDefault: true },
        { modelKey: 'mk-2', provider: 'anthropic', isDefault: false },
      ],
      embedding: [],
    });
    expect(aiModelsOutline('x')).to.deep.equal({});
  });

  it('the entry’s digest moves when a model is repointed and not when its key is rotated', async () => {
    const digests = async (models: unknown): Promise<unknown> => {
      const world = auditedControlPlane();
      await auditedAiModelsSave(new Store() as never, world.transaction, heads)(by, null, 'e', models, ['llm']);
      return world.configEntries(ORG)[0]!.fields['changeDigest'];
    };
    const rotated = { ...MODELS, llm: [{ ...MODELS.llm[0], configuration: { model: 'gpt-x', apiKey: 'other' } }, MODELS.llm[1]] };
    const repointed = { ...MODELS, llm: [{ ...MODELS.llm[0], provider: 'anthropic' }, MODELS.llm[1]] };
    expect(await digests(rotated)).to.equal(await digests(MODELS));
    expect(await digests(repointed)).to.not.equal(await digests(MODELS));
  });

  it('a save the store refuses records nothing and moves neither revision', async () => {
    kv.failing = true;
    const failed = await save()(by, null, 'encrypted:doc', MODELS, ['llm']).catch((error: unknown) => error);
    expect(failed).to.be.instanceOf(Error);
    expect(audit.configEntries(ORG)).to.deep.equal([]);
    expect(audit.transaction.heads.get(ORG) ?? 0).to.equal(0);
    expect(heads.deployment).to.equal(0);
  });

  it('a save that lost the race to another writer records nothing, moves nothing and says so', async () => {
    kv.values.set(configPaths.aiModels, 'encrypted:theirs');
    expect(await save()(by, 'encrypted:stale', 'encrypted:doc', MODELS, ['llm'])).to.equal(false);
    expect(kv.values.get(configPaths.aiModels)).to.equal('encrypted:theirs');
    expect(audit.configEntries(ORG)).to.deep.equal([]);
    expect(audit.transaction.heads.get(ORG) ?? 0).to.equal(0);
    expect(heads.deployment).to.equal(0);
  });

  it('a route handler saves through it: the role assignment is one entry', async () => {
    kv.values.set(configPaths.aiModels, `encrypted:${JSON.stringify(MODELS)}`);
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis(), end: sinon.stub() };
    const next = sinon.stub();
    await updateModelRoles(kv as never, save())(
      { body: { roles: { summarizer: { modelType: 'llm', modelKey: 'mk-2' } } }, user: by.user, headers: {} } as never,
      res as never,
      next,
    );
    expect(next.called).to.equal(false);
    expect(res.status.calledWith(200)).to.equal(true);
    expect(audit.configEntries(ORG).map((entry) => entry.fields['changed'])).to.deep.equal(['roles']);
    expect(heads.deployment).to.equal(1);
    expect(JSON.parse(kv.values.get(configPaths.aiModels)!.replace('encrypted:', '')).modelRoles).to.deep.equal({
      summarizer: { modelType: 'llm', modelKey: 'mk-2' },
    });
  });

  it('without a control plane the save is the store write alone', async () => {
    expect(await plainAiModelsSave(kv as never)(by, null, 'encrypted:plain', MODELS, ['llm'])).to.equal(true);
    expect(kv.values.get(configPaths.aiModels)).to.equal('encrypted:plain');
    expect(audit.configEntries(ORG)).to.deep.equal([]);
  });
});
