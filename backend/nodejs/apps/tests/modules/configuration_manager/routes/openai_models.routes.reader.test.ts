/// <reference types="mocha" />
import { expect } from 'chai';
import express from 'express';
import { AddressInfo } from 'net';
import { Server } from 'http';

import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config';
import { createOpenAIModelsRouter } from '../../../../src/modules/configuration_manager/routes/openai_models.routes';

/** Where the model catalog reads the AI models config: the global key, or an edition's own reader. */
describe('openai models catalog reader', () => {
  const saved = { key: process.env.SECRET_KEY, alg: process.env.ALGORITHM };
  // The service is a process-wide singleton that keeps the first algorithm and key it was given;
  // another test may have left one behind (for example with a CBC algorithm, which rejects our IV).
  const priorInstance = (EncryptionService as unknown as { instance?: unknown }).instance;
  let api: Server | undefined;

  before(() => {
    process.env.SECRET_KEY = 'catalog-reader-test-secret';
    process.env.ALGORITHM = 'aes-256-gcm';
    (EncryptionService as unknown as { instance?: unknown }).instance = undefined;
  });
  afterEach(() => {
    api?.close();
    api = undefined;
  });
  after(() => {
    (EncryptionService as unknown as { instance?: unknown }).instance = priorInstance;
    if (saved.key === undefined) delete process.env.SECRET_KEY;
    else process.env.SECRET_KEY = saved.key;
    if (saved.alg === undefined) delete process.env.ALGORITHM;
    else process.env.ALGORITHM = saved.alg;
  });

  const config = (model: string) => ({
    llm: [{ modelKey: `k-${model}`, provider: 'openAICompatible', configuration: { model }, isDefault: true }],
  });

  async function ids(extra: Record<string, unknown>, globalValue: string | null): Promise<string[]> {
    const bindings: Record<string, unknown> = {
      AuthMiddleware: {
        authenticate: (req: any, _res: any, next: any) => {
          req.user = { orgId: 'o', userId: 'u' };
          next();
        },
      },
      KeyValueStoreService: { get: async () => globalValue },
      ...extra,
    };
    const container = { isBound: (id: string) => id in bindings, get: (id: string) => bindings[id] } as any;
    const app = express();
    app.use('/v1', createOpenAIModelsRouter(container));
    api = await new Promise<Server>((r) => {
      const s = app.listen(0, '127.0.0.1', () => r(s));
    });
    const url = `http://127.0.0.1:${(api.address() as AddressInfo).port}/v1/models`;
    return ((await (await fetch(url)).json()) as any).data.map((m: any) => m.id);
  }

  const stored = (model: string) => {
    const cfg = loadConfigurationManagerConfig();
    return EncryptionService.getInstance(cfg.algorithm, cfg.secretKey).encrypt(JSON.stringify(config(model)));
  };

  it('reads the global key when no reader is bound', async () => {
    expect(await ids({}, stored('global-model'))).to.deep.equal(['global-model']);
  });

  it('uses a bound reader instead of the global key', async () => {
    const out = await ids({ AiModelsReader: async () => config('bound-model') }, stored('global-model'));
    expect(out).to.deep.equal(['bound-model']);
  });
});
