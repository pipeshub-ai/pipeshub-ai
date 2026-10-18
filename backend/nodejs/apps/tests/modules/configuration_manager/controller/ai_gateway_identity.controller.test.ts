/// <reference types="mocha" />
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';

import * as cmConfig from '../../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor';
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command';
import {
  getIdentityPolicy,
  previewIdentityForwarding,
  putIdentityPolicy,
  setConnectionIdentityForwarding,
} from '../../../../src/modules/configuration_manager/controller/ai_gateway_identity.controller';
import { stripAiModelsStoredConfig } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets';
import {
  connectionIdentityForwardingSchema,
  identityPolicySchema,
  identityPreviewSchema,
} from '../../../../src/modules/configuration_manager/validator/validators';

const POLICY_PATH = '/services/aiGateway/identityPolicy';
const MODELS_PATH = '/services/aiModels';

function keyedStore(initial: Record<string, unknown> = {}) {
  const data = new Map<string, string>();
  for (const [key, value] of Object.entries(initial)) {
    data.set(key, `encrypted:${JSON.stringify(value)}`);
  }
  return {
    read(key: string): any {
      const raw = data.get(key);
      return raw ? JSON.parse(raw.replace(/^encrypted:/, '')) : null;
    },
    async get<T>(key: string): Promise<T | null> {
      return (data.get(key) ?? null) as T | null;
    },
    async compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean> {
      if ((data.get(key) ?? null) !== expected) return false;
      data.set(key, next as unknown as string);
      return true;
    },
  };
}

function response() {
  const res = {
    statusCode: 200,
    body: undefined as any,
    status(code: number) {
      this.statusCode = code;
      return this;
    },
    json(body: unknown) {
      this.body = body;
      return this;
    },
  };
  return res;
}

function seed() {
  const entry = (modelKey: string, connectionId: string) => ({
    provider: 'litellmProxy',
    configuration: { model: modelKey, apiKey: 'sk-stored', identityForwardingServiceApiKey: 'sk-svc' },
    modelKey,
    connectionId,
    isMultimodal: false,
    isDefault: false,
    isReasoning: false,
  });
  return {
    llm: [entry('a', 'conn-1'), entry('b', 'conn-1'), entry('c', 'conn-2')],
    modelRoles: {},
  };
}

const forwarding = {
  enabled: true,
  mode: 'headers',
  preset: 'litellm',
  headerTemplates: {},
  serviceAuth: 'apiKey',
};

describe('ai gateway identity', () => {
  beforeEach(() => {
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({
      algorithm: 'aes-256-gcm',
      secretKey: 'a'.repeat(64),
    } as any);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (value: string) => `encrypted:${value}`,
      decrypt: (value: string) => value.replace(/^encrypted:/, ''),
    } as any);
  });

  afterEach(() => sinon.restore());

  describe('identity policy', () => {
    it('answers the restrictive default when nothing is saved', async () => {
      const res = response();
      await getIdentityPolicy(keyedStore() as any)({} as any, res as any, () => undefined);
      expect(res.body.policy).to.include({
        subjectSource: 'userId',
        includeEmail: false,
        pseudonymize: false,
        onMissingIdentity: 'service',
      });
      expect(res.body.policy.groups).to.deep.equal({ enabled: true, allowList: [], maxForwarded: 50 });
    });

    it('saves a policy and reads it back', async () => {
      const store = keyedStore();
      const body = identityPolicySchema.parse({
        body: { includeEmail: true, groups: { allowList: ['g1'], maxForwarded: 10 } },
      }).body;
      const put = response();
      await putIdentityPolicy(store as any)({ body } as any, put as any, () => undefined);
      expect(put.statusCode).to.equal(200);
      expect(store.read(POLICY_PATH).groups.allowList).to.deep.equal(['g1']);

      const got = response();
      await getIdentityPolicy(store as any)({} as any, got as any, () => undefined);
      expect(got.body.policy.includeEmail).to.equal(true);
    });

    it('falls back to the default when the stored blob is damaged', async () => {
      const store = keyedStore({ [POLICY_PATH]: { subjectSource: 'nonsense' } });
      const res = response();
      await getIdentityPolicy(store as any)({} as any, res as any, () => undefined);
      expect(res.body.policy.subjectSource).to.equal('userId');
    });

    it('rejects unknown keys and out-of-range values', () => {
      expect(identityPolicySchema.safeParse({ body: { surprise: 1 } }).success).to.equal(false);
      expect(identityPolicySchema.safeParse({ body: { groups: { maxForwarded: 501 } } }).success).to.equal(false);
      expect(identityPolicySchema.safeParse({ body: { subjectSource: 'ssn' } }).success).to.equal(false);
    });
  });

  describe('connection forwarding', () => {
    async function put(store: any, connectionId: string, body: Record<string, unknown>) {
      const parsed = connectionIdentityForwardingSchema.parse({ params: { connectionId }, body });
      const res = response();
      await setConnectionIdentityForwarding(store)(
        { params: parsed.params, body: parsed.body } as any,
        res as any,
        () => undefined,
      );
      return res;
    }

    it('writes every entry of the connection and no other', async () => {
      const store = keyedStore({ [MODELS_PATH]: seed() });
      const res = await put(store, 'conn-1', forwarding);
      expect(res.body).to.deep.include({ updated: 2 });
      const saved = store.read(MODELS_PATH).llm;
      expect(saved[0].configuration.identityForwardingEnabled).to.equal(true);
      expect(saved[1].configuration.identityForwardingEnabled).to.equal(true);
      expect(saved[2].configuration.identityForwardingEnabled).to.equal(undefined);
    });

    it('keeps the stored service key when none is sent, and clears it on an empty string', async () => {
      const store = keyedStore({ [MODELS_PATH]: seed() });
      await put(store, 'conn-1', forwarding);
      expect(store.read(MODELS_PATH).llm[0].configuration.identityForwardingServiceApiKey).to.equal('sk-svc');
      await put(store, 'conn-1', { ...forwarding, serviceApiKey: '' });
      expect(store.read(MODELS_PATH).llm[0].configuration.identityForwardingServiceApiKey).to.equal(undefined);
      await put(store, 'conn-1', { ...forwarding, serviceApiKey: 'sk-new' });
      expect(store.read(MODELS_PATH).llm[1].configuration.identityForwardingServiceApiKey).to.equal('sk-new');
    });

    it('does not touch credentials or the model', async () => {
      const store = keyedStore({ [MODELS_PATH]: seed() });
      await put(store, 'conn-1', forwarding);
      const first = store.read(MODELS_PATH).llm[0].configuration;
      expect(first.apiKey).to.equal('sk-stored');
      expect(first.model).to.equal('a');
    });

    it('answers 404 for a connection that does not exist', async () => {
      const store = keyedStore({ [MODELS_PATH]: seed() });
      const res = await put(store, 'nope', forwarding);
      expect(res.statusCode).to.equal(404);
    });

    it('rejects a header template with a forbidden shape', () => {
      const bad = connectionIdentityForwardingSchema.safeParse({
        params: { connectionId: 'c' },
        body: { ...forwarding, headerTemplates: { 'bad name': '{{subject}}' } },
      });
      expect(bad.success).to.equal(false);
      const extra = connectionIdentityForwardingSchema.safeParse({
        params: { connectionId: 'c' },
        body: { ...forwarding, surprise: true },
      });
      expect(extra.success).to.equal(false);
    });

    it('keeps the service key out of the public view and lists it as configured', () => {
      const view: any = stripAiModelsStoredConfig({
        llm: [
          {
            ...seed().llm[0],
            configuration: {
              model: 'a',
              identityForwardingEnabled: true,
              identityForwardingPreset: 'litellm',
              identityForwardingServiceApiKey: 'sk-svc',
            },
          },
        ],
      });
      const entry = view.llm[0];
      expect(entry.configuration.identityForwardingEnabled).to.equal(true);
      expect(entry.configuration.identityForwardingServiceApiKey).to.equal(undefined);
      expect(entry.configuredFields).to.include('identityForwardingServiceApiKey');
      expect(JSON.stringify(view)).to.not.include('sk-svc');
    });
  });

  describe('preview', () => {
    let execute: sinon.SinonStub;
    const appConfig = { aiBackend: 'http://ai.internal' } as any;

    beforeEach(() => {
      execute = sinon.stub(AIServiceCommand.prototype, 'execute');
    });

    it('sends the stored connection settings to the Python service', async () => {
      const store = keyedStore({ [MODELS_PATH]: seed() });
      let sent: any;
      execute.callsFake(async function (this: { body: string }) {
        sent = JSON.parse(this.body);
        return { statusCode: 200, data: { success: true } };
      });
      const body = identityPreviewSchema.parse({
        body: { connectionId: 'conn-1', configuration: { identityForwardingEnabled: true } },
      }).body;
      const res = response();
      await previewIdentityForwarding(store as any, appConfig)(
        { body, headers: {} } as any,
        res as any,
        () => undefined,
      );
      expect(res.statusCode).to.equal(200);
      expect(sent.workload).to.equal('chat');
      expect(sent.configuration.identityForwardingEnabled).to.equal(true);
      expect(sent.configuration.identityForwardingServiceApiKey).to.equal('sk-svc');
      expect(sent.configuration.connectionId).to.equal(undefined);
    });

    it('answers 404 for an unknown connection and calls nothing', async () => {
      const store = keyedStore({ [MODELS_PATH]: seed() });
      const res = response();
      await previewIdentityForwarding(store as any, appConfig)(
        { body: { connectionId: 'nope', workload: 'chat', configuration: {} }, headers: {} } as any,
        res as any,
        () => undefined,
      );
      expect(res.statusCode).to.equal(404);
      expect(execute.called).to.equal(false);
    });
  });
});
