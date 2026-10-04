import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import crypto from 'crypto';
import {
  createSlackBotConfig,
  deleteSlackBotConfig,
  getSlackBotConfigs,
  updateSlackBotConfig,
  SLACK_BOT_SETTINGS_UNREADABLE,
  addWebSearchProvider,
  deleteWebSearchProvider,
  getWebSearchProviders,
  updateDefaultWebSearchProvider,
  updateWebSearchProvider,
  updateWebSearchSettings,
} from '../../../../src/modules/configuration_manager/controller/cm_controller';
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config';
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import { CONFIG_SECRET_PLACEHOLDER } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets';
import { InternalServerError } from '../../../../src/libs/errors/http.errors';
import type { NextFunction, Response } from 'express';
import type { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types';
import type { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service';
import type { AppConfig } from '../../../../src/modules/tokens_manager/config/config';

// Drives the real configuration-manager handlers against an in-memory key-value
// store, with real encryption. Only the store and the AI service's HTTP health
// check are faked.

class MemoryKvStore {
  values = new Map<string, string>();
  writes: string[] = [];
  casConflictsLeft = 0;

  async get<T>(key: string): Promise<T | null> {
    return (this.values.get(key) ?? null) as T | null;
  }

  async set<T>(key: string, value: T): Promise<void> {
    this.writes.push(key);
    this.values.set(key, value as unknown as string);
  }

  async compareAndSet<T>(key: string, expected: T | null, next: T): Promise<boolean> {
    if (this.casConflictsLeft > 0) {
      this.casConflictsLeft--;
      return false;
    }
    if ((this.values.get(key) ?? null) !== expected) {
      return false;
    }
    this.writes.push(key);
    this.values.set(key, next as unknown as string);
    return true;
  }
}

interface StoredProvider {
  provider: string;
  providerKey?: string;
  configuration: Record<string, unknown>;
  isDefault?: boolean;
}

interface SlackBotEntry {
  id: string;
  name?: string;
  agentId?: string | null;
  botToken?: string;
}

// The response fields these handlers send, as far as the tests read them.
interface ResponseBody {
  message?: string;
  config?: SlackBotEntry;
  configs?: SlackBotEntry[];
  details?: Record<string, unknown>;
  settings?: Record<string, unknown>;
  providers?: StoredProvider[];
}

interface FakeRes {
  statusCode: number;
  body: ResponseBody | undefined;
  status: sinon.SinonStub;
  json: sinon.SinonStub;
  end: sinon.SinonStub;
}

type Handler = (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => Promise<void>;

function makeRes(): FakeRes {
  const res: FakeRes = {
    statusCode: 200,
    body: undefined,
    status: sinon.stub(),
    json: sinon.stub(),
    end: sinon.stub(),
  };
  res.status.callsFake((code: number) => {
    res.statusCode = code;
    return res;
  });
  res.json.callsFake((body: ResponseBody) => {
    res.body = body;
    return res;
  });
  res.end.returns(res);
  return res;
}

async function run(handler: Handler, req: Record<string, unknown> = {}) {
  const res = makeRes();
  const next = sinon.stub();
  await handler(
    { body: {}, params: {}, headers: {}, ...req } as unknown as AuthenticatedUserRequest,
    res as unknown as Response,
    next,
  );
  // Tests that expect success check `error` is undefined before reading it.
  return { res, error: next.firstCall?.args[0] as Error };
}

describe('Configuration manager stored settings', () => {
  let kv: MemoryKvStore;
  let savedSecretKey: string | undefined;
  let savedHideSecrets: string | undefined;

  const encryptor = () => {
    const cfg = loadConfigurationManagerConfig();
    return EncryptionService.getInstance(cfg.algorithm, cfg.secretKey);
  };
  const seal = (value: unknown) => encryptor().encrypt(JSON.stringify(value));
  const open = <T>(key: string): T =>
    JSON.parse(encryptor().decrypt(kv.values.get(key)!)) as T;
  const store = () => kv as unknown as KeyValueStoreService;
  const openSlackBots = () => open<{ configs: SlackBotEntry[] }>(configPaths.slackBot).configs;

  // Same iv:ciphertext:tag format, sealed with a key the server does not hold,
  // which is what the store looks like after SECRET_KEY changes.
  function sealWithAnotherKey(value: unknown): string {
    const key = crypto.randomBytes(32);
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
    const data = Buffer.concat([cipher.update(JSON.stringify(value), 'utf8'), cipher.final()]);
    return `${iv.toString('hex')}:${data.toString('hex')}:${cipher.getAuthTag().toString('hex')}`;
  }

  before(() => {
    savedSecretKey = process.env.SECRET_KEY;
    process.env.SECRET_KEY = process.env.SECRET_KEY || 'stored-settings-test-secret';
  });

  after(() => {
    if (savedSecretKey === undefined) delete process.env.SECRET_KEY;
    else process.env.SECRET_KEY = savedSecretKey;
  });

  beforeEach(() => {
    kv = new MemoryKvStore();
    savedHideSecrets = process.env.HIDE_SECRET_CONFIG;
  });

  afterEach(() => {
    sinon.restore();
    if (savedHideSecrets === undefined) delete process.env.HIDE_SECRET_CONFIG;
    else process.env.HIDE_SECRET_CONFIG = savedHideSecrets;
  });

  describe('Slack bot settings', () => {
    const existingBot = {
      id: 'bot-1',
      name: 'Support bot',
      botToken: 'xoxb-existing',
      signingSecret: 'signing-existing',
      agentId: 'agent-1',
      createdAt: '2026-01-01T00:00:00.000Z',
      updatedAt: '2026-01-01T00:00:00.000Z',
    };

    describe('when the stored settings cannot be read', () => {
      beforeEach(() => {
        kv.values.set(configPaths.slackBot, sealWithAnotherKey({ configs: [existingBot] }));
      });

      it('refuses to add a bot instead of replacing every stored bot with the new one', async () => {
        const before = kv.values.get(configPaths.slackBot);

        const { res, error } = await run(createSlackBotConfig(store()), {
          body: { name: 'New bot', botToken: 'xoxb-new', signingSecret: 'sig-new' },
        });

        expect(error).to.be.instanceOf(InternalServerError);
        expect(error.message).to.equal(SLACK_BOT_SETTINGS_UNREADABLE);
        expect(res.body).to.be.undefined;
        expect(kv.values.get(configPaths.slackBot)).to.equal(before);
        expect(kv.writes).to.deep.equal([]);
      });

      it('answers a read with an error, not an empty list of bots', async () => {
        const { res, error } = await run(getSlackBotConfigs(store()));

        expect(error).to.be.instanceOf(InternalServerError);
        expect(res.body).to.be.undefined;
      });

      it('refuses updates and deletes too, and leaves the stored value alone', async () => {
        const update = await run(updateSlackBotConfig(store()), {
          params: { configId: 'bot-1' },
          body: { name: 'Renamed', botToken: 'x', signingSecret: 'y' },
        });
        const remove = await run(deleteSlackBotConfig(store()), { params: { configId: 'bot-1' } });

        expect(update.error).to.be.instanceOf(InternalServerError);
        expect(remove.error).to.be.instanceOf(InternalServerError);
        expect(kv.writes).to.deep.equal([]);
      });
    });

    it('treats a store that was never written as having no bots', async () => {
      const { res, error } = await run(getSlackBotConfigs(store()));

      expect(error).to.be.undefined;
      expect(res.body?.configs).to.deep.equal([]);
    });

    it('keeps the existing bots when a new one is added', async () => {
      kv.values.set(configPaths.slackBot, seal({ configs: [existingBot] }));

      const { res, error } = await run(createSlackBotConfig(store()), {
        body: { name: 'New bot', botToken: 'xoxb-new', signingSecret: 'sig-new', agentId: ' agent-2 ' },
      });

      expect(error).to.be.undefined;
      expect(res.body?.config).to.include({ name: 'New bot', agentId: 'agent-2' });
      const stored = openSlackBots();
      expect(stored.map((c) => c.id)).to.deep.equal(['bot-1', res.body?.config?.id]);
    });

    it('refuses to link one agent to two bots', async () => {
      kv.values.set(configPaths.slackBot, seal({ configs: [existingBot] }));

      const { error } = await run(createSlackBotConfig(store()), {
        body: { name: 'Clone', botToken: 'x', signingSecret: 'y', agentId: 'agent-1' },
      });

      expect(error.message).to.match(/already linked/);
      expect(kv.writes).to.deep.equal([]);
    });

    it('retries after a concurrent write and gives up with a plain message after five tries', async () => {
      kv.values.set(configPaths.slackBot, seal({ configs: [] }));
      kv.casConflictsLeft = 5;
      const clock = sinon.useFakeTimers({ toFake: ['setTimeout'] });

      const pending = run(createSlackBotConfig(store()), {
        body: { name: 'New bot', botToken: 'x', signingSecret: 'y' },
      });
      await clock.runAllAsync();
      const { error } = await pending;

      expect(error.message).to.match(/concurrent modification. Please try again/);
      expect(kv.writes).to.deep.equal([]);
    });

    it('updates one bot and leaves the others untouched', async () => {
      const other = { ...existingBot, id: 'bot-2', agentId: 'agent-2', name: 'Sales bot' };
      kv.values.set(configPaths.slackBot, seal({ configs: [existingBot, other] }));

      const { error } = await run(updateSlackBotConfig(store()), {
        params: { configId: 'bot-1' },
        body: { name: 'Support bot v2', botToken: 'xoxb-rotated', signingSecret: 'sig-rotated' },
      });

      expect(error).to.be.undefined;
      const stored = openSlackBots();
      expect(stored[0]).to.include({ id: 'bot-1', name: 'Support bot v2', botToken: 'xoxb-rotated' });
      expect(stored[1]).to.deep.equal(other);
    });
  });

  describe('web search providers', () => {
    const aiBackend = 'http://ai.test';
    const appConfig = { aiBackend } as unknown as AppConfig;
    let fetchStub: sinon.SinonStub;
    let healthChecks: Array<{ provider: string; configuration: Record<string, unknown> }>;
    let healthAnswer: { status: number; body: unknown };
    let agentsUsing: unknown[] | Error;

    const serper = {
      provider: 'serper',
      providerKey: 'key-serper',
      configuration: { apiKey: 'serper-real-key' },
      isDefault: true,
    };
    const tavily = {
      provider: 'tavily',
      providerKey: 'key-tavily',
      configuration: { apiKey: 'tavily-real-key' },
      isDefault: false,
    };

    beforeEach(() => {
      healthChecks = [];
      healthAnswer = { status: 200, body: { status: 'healthy' } };
      agentsUsing = [];
      fetchStub = sinon.stub(globalThis, 'fetch').callsFake((async (url: string, init: RequestInit) => {
        if (url === `${aiBackend}/api/v1/web-search-health-check`) {
          healthChecks.push(JSON.parse(String(init.body)) as (typeof healthChecks)[number]);
          return new Response(JSON.stringify(healthAnswer.body), { status: healthAnswer.status });
        }
        if (url.startsWith(`${aiBackend}/api/v1/agent/web-search-usage/`)) {
          if (agentsUsing instanceof Error) throw agentsUsing;
          return new Response(JSON.stringify({ success: true, agents: agentsUsing }), { status: 200 });
        }
        throw new Error(`unexpected fetch ${url}`);
      }) as unknown as typeof fetch);
    });

    function stored() {
      return open<{ providers: StoredProvider[]; settings?: Record<string, unknown> }>(
        configPaths.webSearch,
      );
    }

    it('refuses to save a provider whose key fails the health check', async () => {
      healthAnswer = { status: 400, body: { error: 'Invalid API key' } };

      const { res } = await run(addWebSearchProvider(store(), appConfig), {
        body: { provider: 'serper', configuration: { apiKey: 'bad' } },
      });

      expect(res.statusCode).to.equal(400);
      expect(res.body?.message).to.equal('Invalid API key');
      expect(kv.values.has(configPaths.webSearch)).to.be.false;
    });

    it('asks for both provider and configuration before calling anything', async () => {
      const { res } = await run(addWebSearchProvider(store(), appConfig), {
        body: { provider: 'serper' },
      });

      expect(res.statusCode).to.equal(400);
      expect(fetchStub.called).to.be.false;
    });

    it('makes the first provider the default, stores it encrypted, and moves the default on request', async () => {
      const first = await run(addWebSearchProvider(store(), appConfig), {
        body: { provider: 'serper', configuration: { apiKey: 'serper-real-key' } },
      });
      const second = await run(addWebSearchProvider(store(), appConfig), {
        body: { provider: 'tavily', configuration: { apiKey: 'tavily-real-key' }, isDefault: true },
      });

      expect(first.res.body?.details?.isDefault).to.be.true;
      expect(second.res.body?.details?.isDefault).to.be.true;
      expect(kv.values.get(configPaths.webSearch)).to.not.include('serper-real-key');
      const providers = stored().providers;
      expect(providers.map((p) => [p.provider, p.isDefault])).to.deep.equal([
        ['serper', false],
        ['tavily', true],
      ]);
    });

    it('keeps the stored key when the form sends the masked placeholder back', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));

      const { res } = await run(updateWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-serper' },
        body: { provider: 'serper', configuration: { apiKey: CONFIG_SECRET_PLACEHOLDER }, isDefault: true },
      });

      expect(res.statusCode).to.equal(200);
      expect(healthChecks[0]?.configuration.apiKey).to.equal('serper-real-key');
      expect(stored().providers[0]?.configuration.apiKey).to.equal('serper-real-key');
    });

    it('does not save an update that lost a race with another write', async () => {
      const original = seal({ providers: [serper] });
      kv.values.set(configPaths.webSearch, original);
      kv.casConflictsLeft = 1;

      const { res } = await run(updateWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-serper' },
        body: { provider: 'serper', configuration: { apiKey: 'serper-new-key' } },
      });

      expect(res.statusCode).to.equal(409);
      expect(kv.values.get(configPaths.webSearch)).to.equal(original);
    });

    it('answers 404 for an unknown provider, and when nothing is configured yet', async () => {
      const empty = await run(updateWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'nope' },
        body: { provider: 'serper', configuration: { apiKey: 'x' } },
      });
      kv.values.set(configPaths.webSearch, seal({ providers: [serper] }));
      const unknown = await run(updateWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'nope' },
        body: { provider: 'serper', configuration: { apiKey: 'x' } },
      });

      expect(empty.res.statusCode).to.equal(404);
      expect(unknown.res.statusCode).to.equal(404);
      expect(healthChecks).to.have.length(0);
    });

    it('will not delete a provider that agents still use', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));
      agentsUsing = [{ id: 'agent-1', name: 'Researcher' }];

      const { res } = await run(deleteWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-serper' },
      });

      expect(res.statusCode).to.equal(409);
      expect(res.body?.message).to.match(/used by 1 agent\./);
      expect(stored().providers).to.have.length(2);
    });

    it('hands the default to the next provider when the default one is deleted', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));

      const { res } = await run(deleteWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-serper' },
      });

      expect(res.statusCode).to.equal(200);
      expect(res.body?.details).to.include({ provider: 'serper', wasDefault: true });
      expect(stored().providers).to.deep.equal([{ ...tavily, isDefault: true }]);
    });

    it('still deletes when the agent-usage check cannot be reached', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));
      agentsUsing = new Error('ai service down');

      const { res } = await run(deleteWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-tavily' },
      });

      expect(res.statusCode).to.equal(200);
      expect(stored().providers.map((p) => p.provider)).to.deep.equal(['serper']);
    });

    it('does not move the default to a provider that fails its health check', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));
      healthAnswer = { status: 502, body: { error: 'Provider unreachable' } };

      const { res } = await run(updateDefaultWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-tavily' },
      });

      expect(res.statusCode).to.equal(502);
      expect(healthChecks[0]).to.deep.equal({ provider: 'tavily', configuration: { apiKey: 'tavily-real-key' } });
      expect(stored().providers.map((p) => p.isDefault)).to.deep.equal([true, false]);
    });

    it('moves the default to a healthy provider', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));

      const { res } = await run(updateDefaultWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'key-tavily' },
      });

      expect(res.statusCode).to.equal(200);
      expect(stored().providers.map((p) => p.isDefault)).to.deep.equal([false, true]);
    });

    it('makes the built-in DuckDuckGo the default by clearing every stored default', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));

      const { res } = await run(updateDefaultWebSearchProvider(store(), appConfig), {
        params: { providerKey: 'duckduckgo' },
      });

      expect(res.statusCode).to.equal(200);
      expect(stored().providers.map((p) => p.isDefault)).to.deep.equal([false, false]);
      expect(healthChecks).to.have.length(0);
    });

    it('saves the image settings and keeps the providers', async () => {
      kv.values.set(configPaths.webSearch, seal({ providers: [serper] }));

      const { res, error } = await run(updateWebSearchSettings(store()), {
        body: { includeImages: true, maxImages: 5 },
      });

      expect(error).to.be.undefined;
      expect(res.body?.settings).to.deep.equal({ includeImages: true, maxImages: 5 });
      expect(stored()).to.deep.equal({
        providers: [serper],
        settings: { includeImages: true, maxImages: 5 },
      });
    });

    it('masks stored API keys in the list any signed-in member can read, when secrets are hidden', async () => {
      process.env.HIDE_SECRET_CONFIG = 'true';
      kv.values.set(configPaths.webSearch, seal({ providers: [serper, tavily] }));

      const { res } = await run(getWebSearchProviders(store()));

      expect(res.statusCode).to.equal(200);
      expect(JSON.stringify(res.body)).to.not.include('real-key');
      expect(res.body?.providers?.map((p) => p.provider)).to.deep.equal(['duckduckgo', 'serper', 'tavily']);
      expect(res.body?.providers?.[0]?.isDefault).to.be.false;
    });

    it('answers a failing store with an error, not an empty provider list', async () => {
      sinon.stub(kv, 'get').rejects(new Error('etcd unavailable'));

      const { res, error } = await run(getWebSearchProviders(store()));

      expect(error).to.be.instanceOf(Error);
      expect(res.body).to.be.undefined;
    });
  });
});
