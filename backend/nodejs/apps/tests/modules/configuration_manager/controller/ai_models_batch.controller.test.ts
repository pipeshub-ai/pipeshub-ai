/// <reference types="mocha" />
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import fs from 'fs';
import path from 'path';

import * as cmConfig from '../../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor';
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command';
import {
  batchAddAIModels,
  discoverAIModels,
  rotateConnectionCredentials,
} from '../../../../src/modules/configuration_manager/controller/ai_models_batch.controller';
import { AIModelsConfig } from '../../../../src/modules/configuration_manager/types/ai-models.types';

function identityCipher() {
  return {
    encrypt: (value: string) => `encrypted:${value}`,
    decrypt: (value: string) => value.replace(/^encrypted:/, ''),
  };
}

function memoryStore(initial?: AIModelsConfig) {
  let stored: string | null = initial ? `encrypted:${JSON.stringify(initial)}` : null;
  return {
    snapshot(): AIModelsConfig | null {
      if (!stored) return null;
      return JSON.parse(stored.replace(/^encrypted:/, '')) as AIModelsConfig;
    },
    async get<T>(): Promise<T | null> {
      return stored as T | null;
    },
    async compareAndSet<T>(_key: string, expected: T | null, next: T): Promise<boolean> {
      if ((stored as T | null) !== expected) return false;
      stored = next as string;
      return true;
    },
  };
}

function sseResponse() {
  const events: { event: string; data: unknown }[] = [];
  const closeListeners: Array<() => void> = [];
  const res = {
    headersSent: false,
    statusCode: 200,
    writableEnded: false,
    writableFinished: false,
    body: undefined as unknown,
    on(event: string, listener: () => void) {
      if (event === 'close') closeListeners.push(listener);
      return this;
    },
    emitClose() {
      for (const listener of closeListeners) listener();
    },
    setHeader() {
      return this;
    },
    flushHeaders() {
      this.headersSent = true;
    },
    write(chunk: string) {
      const event = /event: (.+)/.exec(chunk)?.[1] ?? '';
      const data = /data: (.+)/.exec(chunk)?.[1] ?? '{}';
      events.push({ event, data: JSON.parse(data) });
    },
    end() {
      this.writableEnded = true;
      this.writableFinished = true;
      return this;
    },
    status(code: number) {
      this.statusCode = code;
      return this;
    },
    json(body: unknown) {
      this.body = body;
      return this;
    },
  };
  return { res, events };
}

const appConfig = { aiBackend: 'http://ai.internal', cmBackend: 'http://cm.internal' } as any;

function seedConfig(): AIModelsConfig {
  return {
    llm: [
      {
        provider: 'openAI',
        configuration: { model: 'gpt-4o', apiKey: 'sk-stored' },
        modelKey: 'llm-1',
        connectionId: 'conn-1',
        isMultimodal: false,
        isDefault: true,
        isReasoning: false,
      },
      {
        provider: 'openAI',
        configuration: { model: 'gpt-4o-mini', apiKey: 'sk-stored' },
        modelKey: 'llm-2',
        connectionId: 'conn-1',
        isMultimodal: false,
        isDefault: false,
        isReasoning: false,
      },
    ],
    modelRoles: {},
  };
}

describe('ai model discover, batch, and rotate', () => {
  let execute: sinon.SinonStub;

  beforeEach(() => {
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({
      algorithm: 'aes-256-gcm',
      secretKey: 'a'.repeat(64),
    } as any);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns(identityCipher() as any);
    execute = sinon.stub(AIServiceCommand.prototype, 'execute');
  });

  afterEach(() => {
    sinon.restore();
  });

  it('merges a stored key for discover and does not echo it', async () => {
    const store = memoryStore(seedConfig());
    let sentBody = '';
    execute.callsFake(async function (this: { body: string }) {
      sentBody = this.body;
      return { statusCode: 200, data: { success: true, models: [{ id: 'gpt-4o' }] } };
    });
    const { res } = sseResponse();
    const req = {
      body: { provider: 'openAI', modelKey: 'llm-1', configuration: { model: 'gpt-4o' } },
      headers: {},
    } as any;

    await discoverAIModels(store as any, appConfig)(req, res as any, () => undefined);

    const sent = JSON.parse(sentBody);
    expect(sent.configuration.apiKey).to.equal('sk-stored');
    expect(JSON.stringify(res.body)).to.not.include('sk-stored');
  });

  it('rejects a provider that does not match the saved model', async () => {
    const store = memoryStore(seedConfig());
    const { res } = sseResponse();
    await discoverAIModels(store as any, appConfig)(
      {
        body: { provider: 'anthropic', modelKey: 'llm-1', configuration: { endpoint: 'https://attacker.example/v1' } },
        headers: {},
      } as any,
      res as any,
      () => undefined,
    );
    expect(res.statusCode).to.equal(400);
    expect(execute.called).to.equal(false);
  });

  it('keeps the saved endpoint when stored credentials are merged', async () => {
    const initial = seedConfig();
    const first = initial.llm?.[0];
    if (!first) throw new Error('seed missing');
    first.configuration = {
      model: 'gpt-4o',
      apiKey: 'sk-stored',
      endpoint: 'https://api.openai.com/v1',
    };
    const store = memoryStore(initial);
    let sentBody = '';
    execute.callsFake(async function (this: { body: string }) {
      sentBody = this.body;
      return { statusCode: 200, data: { success: true, models: [] } };
    });
    const { res } = sseResponse();
    await discoverAIModels(store as any, appConfig)(
      {
        body: {
          provider: 'openAI',
          modelKey: 'llm-1',
          configuration: { endpoint: 'https://attacker.example/v1', model: 'gpt-4o' },
        },
        headers: {},
      } as any,
      res as any,
      () => undefined,
    );
    const sent = JSON.parse(sentBody);
    expect(sent.provider).to.equal('openAI');
    expect(sent.configuration.apiKey).to.equal('sk-stored');
    expect(sent.configuration.endpoint).to.equal('https://api.openai.com/v1');
    expect(res.statusCode).to.equal(200);
  });

  it('returns 404 when the model key is unknown', async () => {
    const store = memoryStore(seedConfig());
    const { res } = sseResponse();
    await discoverAIModels(store as any, appConfig)(
      { body: { provider: 'openAI', modelKey: 'missing', configuration: {} }, headers: {} } as any,
      res as any,
      () => undefined,
    );
    expect(res.statusCode).to.equal(404);
    expect(execute.called).to.equal(false);
  });

  it('saves only healthy models, applies the default once, and emits one event', async () => {
    const store = memoryStore();
    let inFlight = 0;
    let maxInFlight = 0;
    execute.callsFake(async function (this: { body: string }) {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await Promise.resolve();
      inFlight -= 1;
      const body = JSON.parse(this.body);
      const model = body.configuration.model as string;
      if (model === 'bad') {
        return { statusCode: 400, data: { message: 'rejected' } };
      }
      return { statusCode: 200, data: { status: 'healthy' } };
    });
    const eventsPublished: string[] = [];
    const eventService = {
      start: async () => undefined,
      stop: async () => undefined,
      publishEvent: async (event: { eventType: string }) => {
        eventsPublished.push(event.eventType);
      },
    };
    const { res, events } = sseResponse();
    const req = {
      body: {
        modelType: 'llm',
        provider: 'openAI',
        configuration: { apiKey: 'sk-new' },
        defaultModel: 'gpt-b',
        models: [
          { model: 'gpt-a', isMultimodal: false, isReasoning: false },
          { model: 'bad', isMultimodal: false, isReasoning: false },
          { model: 'gpt-b', isMultimodal: true, isReasoning: false },
          { model: 'gpt-c', isMultimodal: false, isReasoning: true },
        ],
      },
      headers: {},
      on: () => undefined,
    } as any;

    await batchAddAIModels(store as any, eventService as any, appConfig)(req, res as any, () => undefined);

    const saved = store.snapshot();
    const names = (saved?.llm ?? []).map((entry) => entry.configuration.model).sort();
    expect(names).to.deep.equal(['gpt-a', 'gpt-b', 'gpt-c']);
    const connectionIds = new Set((saved?.llm ?? []).map((entry) => entry.connectionId));
    expect(connectionIds.size).to.equal(1);
    const defaults = (saved?.llm ?? []).filter((entry) => entry.isDefault).map((entry) => entry.configuration.model);
    expect(defaults).to.deep.equal(['gpt-b']);
    expect(eventsPublished).to.have.length(1);
    expect(maxInFlight).to.be.at.most(3);
    const failed = events.filter((event) => event.event === 'progress' && (event.data as { status?: string }).status === 'failed');
    expect(failed).to.have.length(1);
    expect(JSON.stringify(saved)).to.not.include('bad');
  });

  it('saves nothing and emits no event when every model fails', async () => {
    const store = memoryStore();
    execute.resolves({ statusCode: 400, data: { message: 'no' } });
    const eventService = {
      start: async () => undefined,
      stop: async () => undefined,
      publishEvent: async () => {
        throw new Error('should not publish');
      },
    };
    const { res } = sseResponse();
    await batchAddAIModels(store as any, eventService as any, appConfig)(
      {
        body: {
          modelType: 'llm',
          provider: 'openAI',
          configuration: { apiKey: 'sk' },
          models: [{ model: 'nope', isMultimodal: false, isReasoning: false }],
        },
        headers: {},
        on: () => undefined,
      } as any,
      res as any,
      () => undefined,
    );
    expect(store.snapshot()?.llm ?? []).to.deep.equal([]);
  });

  it('does not save when the client disconnects during the health check', async () => {
    const store = memoryStore();
    const { res, events } = sseResponse();
    execute.callsFake(async () => {
      res.emitClose();
      return { statusCode: 200, data: { status: 'healthy' } };
    });
    const eventService = {
      start: async () => undefined,
      stop: async () => undefined,
      publishEvent: async () => {
        throw new Error('should not publish');
      },
    };
    await batchAddAIModels(store as any, eventService as any, appConfig)(
      {
        body: {
          modelType: 'llm',
          provider: 'openAI',
          configuration: { apiKey: 'sk' },
          models: [
            { model: 'gpt-a', isMultimodal: false, isReasoning: false },
            { model: 'gpt-b', isMultimodal: false, isReasoning: false },
          ],
        },
        headers: {},
        on: () => undefined,
      } as any,
      res as any,
      () => undefined,
    );
    expect(store.snapshot()?.llm ?? []).to.deep.equal([]);
    const done = events.find((event) => event.event === 'done');
    expect((done?.data as { aborted?: boolean }).aborted).to.equal(true);
  });

  const quietEvents = {
    start: async () => undefined,
    stop: async () => undefined,
    publishEvent: async () => undefined,
  };

  async function runBatch(store: ReturnType<typeof memoryStore>, body: Record<string, unknown>) {
    const { res, events } = sseResponse();
    await batchAddAIModels(store as any, quietEvents as any, appConfig)(
      { body: { modelType: 'llm', provider: 'openAI', ...body }, headers: {}, on: () => undefined } as any,
      res as any,
      () => undefined,
    );
    return { res, events };
  }

  const healthyUnless = (failing: string) =>
    async function (this: { body: string }) {
      const model = JSON.parse(this.body).configuration.model as string;
      return model === failing
        ? { statusCode: 400, data: { message: 'rejected' } }
        : { statusCode: 200, data: { status: 'healthy' } };
    };

  it('joins an existing connection when a matching connectionId is sent', async () => {
    const store = memoryStore(seedConfig());
    execute.callsFake(healthyUnless('none'));

    const { events } = await runBatch(store, {
      connectionId: 'conn-1',
      configuration: { apiKey: 'sk-stored' },
      models: [{ model: 'gpt-4.1', isMultimodal: true, isReasoning: false }],
    });

    const llm = store.snapshot()?.llm ?? [];
    const added = llm.find((entry) => entry.configuration.model === 'gpt-4.1');
    expect(added?.connectionId).to.equal('conn-1');
    expect(added?.isDefault).to.equal(false);
    expect(llm.filter((entry) => entry.isDefault).map((entry) => entry.modelKey)).to.deep.equal(['llm-1']);
    const done = events.find((event) => event.event === 'done');
    expect((done?.data as { connectionId?: string }).connectionId).to.equal('conn-1');
  });

  it('rejects a connectionId that belongs to another provider without checking or saving', async () => {
    const store = memoryStore(seedConfig());
    const before = JSON.stringify(store.snapshot());

    const { res } = await runBatch(store, {
      provider: 'anthropic',
      connectionId: 'conn-1',
      configuration: { apiKey: 'sk-ant' },
      models: [{ model: 'claude-sonnet', isMultimodal: false, isReasoning: false }],
    });

    expect(res.statusCode).to.equal(400);
    expect(execute.called).to.equal(false);
    expect(JSON.stringify(store.snapshot())).to.equal(before);
  });

  it('does not copy a shared friendly name onto models without their own', async () => {
    const store = memoryStore();
    execute.callsFake(healthyUnless('none'));

    await runBatch(store, {
      configuration: { apiKey: 'sk', modelFriendlyName: 'Shared' },
      models: [
        { model: 'gpt-a', isMultimodal: false, isReasoning: false },
        { model: 'gpt-b', modelFriendlyName: 'Bee', isMultimodal: false, isReasoning: false },
      ],
    });

    const byModel = new Map((store.snapshot()?.llm ?? []).map((entry) => [entry.configuration.model, entry]));
    expect(byModel.get('gpt-a')?.configuration.modelFriendlyName).to.equal(undefined);
    expect(byModel.get('gpt-a')?.modelFriendlyName).to.equal(undefined);
    expect(byModel.get('gpt-b')?.modelFriendlyName).to.equal('Bee');
  });

  it('still sets exactly one default when the chosen default fails in an empty bucket', async () => {
    const store = memoryStore();
    execute.callsFake(healthyUnless('bad'));

    await runBatch(store, {
      configuration: { apiKey: 'sk' },
      defaultModel: 'bad',
      models: [
        { model: 'gpt-a', isMultimodal: false, isReasoning: false },
        { model: 'bad', isMultimodal: false, isReasoning: false },
        { model: 'gpt-b', isMultimodal: false, isReasoning: false },
      ],
    });

    const llm = store.snapshot()?.llm ?? [];
    expect(llm).to.have.length(2);
    expect(llm.filter((entry) => entry.isDefault)).to.have.length(1);
  });

  it('does not write credentials when the health check fails', async () => {
    const store = memoryStore(seedConfig());
    execute.resolves({ statusCode: 400, data: { message: 'bad key' } });
    const { res } = sseResponse();
    await rotateConnectionCredentials(store as any, appConfig)(
      { params: { connectionId: 'conn-1' }, body: { configuration: { apiKey: 'sk-new' } }, headers: {} } as any,
      res as any,
      () => undefined,
    );
    expect(res.statusCode).to.equal(400);
    const saved = store.snapshot();
    expect((saved?.llm ?? []).every((entry) => entry.configuration.apiKey === 'sk-stored')).to.equal(true);
  });

  it('rotates the key and leaves the saved endpoint and model id alone', async () => {
    const initial = seedConfig();
    for (const entry of initial.llm ?? []) {
      entry.configuration = {
        ...entry.configuration,
        endpoint: 'https://api.openai.com/v1',
      };
    }
    const store = memoryStore(initial);
    let sentBody = '';
    execute.callsFake(async function (this: { body: string }) {
      sentBody = this.body;
      return { statusCode: 200, data: {} };
    });
    const { res } = sseResponse();
    await rotateConnectionCredentials(store as any, appConfig)(
      {
        params: { connectionId: 'conn-1' },
        body: {
          configuration: {
            apiKey: 'sk-rotated',
            endpoint: 'https://attacker.example/v1',
            model: 'hijacked',
          },
        },
        headers: {},
      } as any,
      res as any,
      () => undefined,
    );
    expect(res.statusCode).to.equal(200);
    const sent = JSON.parse(sentBody);
    expect(sent.configuration.apiKey).to.equal('sk-rotated');
    expect(sent.configuration.endpoint).to.equal('https://api.openai.com/v1');
    expect(sent.configuration.model).to.equal('gpt-4o');
    const saved = store.snapshot()?.llm ?? [];
    expect(saved.map((entry) => entry.configuration.apiKey)).to.deep.equal(['sk-rotated', 'sk-rotated']);
    expect(saved.every((entry) => entry.configuration.endpoint === 'https://api.openai.com/v1')).to.equal(true);
    expect(saved.map((entry) => entry.configuration.model).sort()).to.deep.equal(['gpt-4o', 'gpt-4o-mini']);
  });

  it('updates every member of the connection after a healthy check', async () => {
    const store = memoryStore(seedConfig());
    execute.resolves({ statusCode: 200, data: {} });
    const { res } = sseResponse();
    await rotateConnectionCredentials(store as any, appConfig)(
      { params: { connectionId: 'conn-1' }, body: { configuration: { apiKey: 'sk-rotated' } }, headers: {} } as any,
      res as any,
      () => undefined,
    );
    expect(res.statusCode).to.equal(200);
    const keys = (store.snapshot()?.llm ?? []).map((entry) => entry.configuration.apiKey);
    expect(keys).to.deep.equal(['sk-rotated', 'sk-rotated']);
  });

  it('checks only the embedding that will be active and does not promote another', async () => {
    const store = memoryStore();
    const bodies: { model?: string; becomesActive?: boolean }[] = [];
    execute.callsFake(async function (this: { body: string }) {
      const body = JSON.parse(this.body);
      bodies.push({ model: body.configuration.model, becomesActive: body.becomesActive });
      return body.configuration.model === 'bad-embed'
        ? { statusCode: 400, data: { message: 'rejected' } }
        : { statusCode: 200, data: { status: 'healthy' } };
    });
    const { res } = sseResponse();
    await batchAddAIModels(store as any, quietEvents as any, appConfig)(
      {
        body: {
          modelType: 'embedding',
          provider: 'openAI',
          configuration: { apiKey: 'sk' },
          defaultModel: 'bad-embed',
          models: [
            { model: 'bad-embed', isMultimodal: false, isReasoning: false },
            { model: 'text-embedding-3-small', isMultimodal: false, isReasoning: false },
          ],
        },
        headers: {},
        on: () => undefined,
      } as any,
      res as any,
      () => undefined,
    );
    const byModel = new Map(bodies.map((body) => [body.model, body.becomesActive]));
    expect(byModel.get('bad-embed')).to.equal(true);
    expect(byModel.get('text-embedding-3-small')).to.equal(false);
    const saved = store.snapshot()?.embedding ?? [];
    expect(saved.map((entry) => entry.configuration.model)).to.deep.equal(['text-embedding-3-small']);
    expect(saved.every((entry) => entry.isDefault === false)).to.equal(true);
  });

  it('does not let credential rotation resize the embedding collection', async () => {
    const store = memoryStore({
      embedding: [
        {
          provider: 'openAI',
          configuration: { model: 'text-embedding-3-small', apiKey: 'sk-stored', endpoint: 'https://api.openai.com/v1' },
          modelKey: 'emb-1',
          connectionId: 'conn-emb',
          isMultimodal: false,
          isDefault: true,
          isReasoning: false,
        },
      ],
      modelRoles: {},
    });
    let sentBody = '';
    execute.callsFake(async function (this: { body: string }) {
      sentBody = this.body;
      return { statusCode: 200, data: {} };
    });
    const { res } = sseResponse();
    await rotateConnectionCredentials(store as any, appConfig)(
      { params: { connectionId: 'conn-emb' }, body: { configuration: { apiKey: 'sk-rotated' } }, headers: {} } as any,
      res as any,
      () => undefined,
    );
    expect(res.statusCode).to.equal(200);
    const sent = JSON.parse(sentBody);
    expect(sent.becomesActive).to.equal(false);
    expect(sent.configuration.endpoint).to.equal('https://api.openai.com/v1');
  });

  it('registers the literal routes before the model type param', () => {
    const source = fs.readFileSync(
      path.join(
        __dirname,
        '../../../../src/modules/configuration_manager/routes/cm_routes.ts',
      ),
      'utf8',
    );
    const batch = source.indexOf("'/ai-models/providers/batch'");
    const typed = source.indexOf("'/ai-models/:modelType'");
    expect(batch).to.be.greaterThan(-1);
    expect(batch).to.be.lessThan(typed);
  });
});
