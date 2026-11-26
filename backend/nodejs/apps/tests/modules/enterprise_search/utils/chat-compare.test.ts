import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';
import * as platformSettings from '../../../../src/modules/configuration_manager/utils/util';
import { PLATFORM_FEATURE_FLAGS } from '../../../../src/modules/configuration_manager/constants/constants';
import {
  CHAT_COMPARE_DISABLED_MESSAGE,
  ChatCompareFeature,
  MAX_COMPARE_MODELS,
  requireChatCompareEnabled,
  setChatCompareSettingsSource,
} from '../../../../src/modules/enterprise_search/utils/chat-compare-feature';
import {
  addMessageStreamParamsSchema,
  compareBranchParamsSchema,
  compareChooseParamsSchema,
  enterpriseSearchStreamCreateSchema,
} from '../../../../src/modules/enterprise_search/validators/es_validators';

const OID = 'a'.repeat(24);

const stubStore = (connected: boolean): sinon.SinonStub =>
  sinon
    .stub(ChatCompareFeature, 'settingsStore')
    .returns({ isConnected: () => connected } as never);

const stubFlags = (featureFlags: Record<string, boolean>): sinon.SinonStub =>
  sinon
    .stub(platformSettings, 'getPlatformSettingsFromStore')
    .resolves({ fileUploadMaxSizeBytes: 1, featureFlags });

describe('enterprise_search/utils/chat-compare-feature', () => {
  beforeEach(() => ChatCompareFeature.resetCache());
  afterEach(() => {
    sinon.restore();
    setChatCompareSettingsSource();
    ChatCompareFeature.resetCache();
    delete process.env.DOC_STORE;
  });

  it('allows two models per question', () => {
    expect(MAX_COMPARE_MODELS).to.equal(2);
  });

  it('is declared as a Labs flag, off by default', () => {
    const flag = PLATFORM_FEATURE_FLAGS.find(
      (f) => f.key === 'ENABLE_CHAT_COMPARE',
    );
    expect(flag).to.include({ defaultEnabled: false });
    expect(flag?.label).to.be.a('string').and.not.equal('');
    expect(flag?.description).to.be.a('string').and.not.equal('');
  });

  it('is on only when the flag is on', async () => {
    stubStore(true);
    stubFlags({ ENABLE_CHAT_COMPARE: true });
    expect(await ChatCompareFeature.isEnabled()).to.equal(true);
    ChatCompareFeature.resetCache();
    sinon.restore();
    stubStore(true);
    stubFlags({});
    expect(await ChatCompareFeature.isEnabled()).to.equal(false);
  });

  it('fails closed when the settings store is unreadable or disconnected', async () => {
    stubStore(false);
    expect(await ChatCompareFeature.isEnabled()).to.equal(false);
    ChatCompareFeature.resetCache();
    sinon.restore();
    stubStore(true);
    sinon
      .stub(platformSettings, 'getPlatformSettingsFromStore')
      .rejects(new Error('down'));
    expect(await ChatCompareFeature.isEnabled()).to.equal(false);
  });

  it('stays off on the PostgreSQL document store', async () => {
    process.env.DOC_STORE = 'postgres';
    stubStore(true);
    stubFlags({ ENABLE_CHAT_COMPARE: true });
    expect(await ChatCompareFeature.isEnabled()).to.equal(false);
  });

  it('the route guard answers 403 while off and continues while on', async () => {
    stubStore(true);
    const flags = stubFlags({});
    const off = sinon.spy();
    await requireChatCompareEnabled({} as never, {} as never, off);
    const error = off.firstCall.args[0];
    expect(error).to.be.instanceOf(ForbiddenError);
    expect(error.message).to.equal(CHAT_COMPARE_DISABLED_MESSAGE);

    ChatCompareFeature.resetCache();
    flags.resolves({
      fileUploadMaxSizeBytes: 1,
      featureFlags: { ENABLE_CHAT_COMPARE: true },
    });
    const on = sinon.spy();
    await requireChatCompareEnabled({} as never, {} as never, on);
    expect(on.firstCall.args).to.deep.equal([]);
  });

  it('reads the settings once for calls close together', async () => {
    stubStore(true);
    const read = stubFlags({ ENABLE_CHAT_COMPARE: true });
    await ChatCompareFeature.isEnabled();
    await ChatCompareFeature.isEnabled();
    expect(read.calledOnce).to.equal(true);
  });

  it('reads the settings again once the cached answer is older than 5 seconds', async () => {
    const clock = sinon.useFakeTimers({ now: Date.now(), toFake: ['Date'] });
    stubStore(true);
    const read = stubFlags({ ENABLE_CHAT_COMPARE: true });
    await ChatCompareFeature.isEnabled();
    clock.tick(4_999);
    await ChatCompareFeature.isEnabled();
    expect(read.calledOnce).to.equal(true);
    clock.tick(2);
    await ChatCompareFeature.isEnabled();
    expect(read.calledTwice).to.equal(true);
  });

  it('does not cache a failed read, so the next call tries again', async () => {
    stubStore(true);
    const read = sinon
      .stub(platformSettings, 'getPlatformSettingsFromStore')
      .onFirstCall()
      .rejects(new Error('down'))
      .onSecondCall()
      .resolves({
        fileUploadMaxSizeBytes: 1,
        featureFlags: { ENABLE_CHAT_COMPARE: true },
      });
    expect(await ChatCompareFeature.isEnabled()).to.equal(false);
    expect(await ChatCompareFeature.isEnabled()).to.equal(true);
    expect(read.calledTwice).to.equal(true);
  });

  describe('a supplied settings source', () => {
    // One store per workspace, told apart by the object the reader is handed.
    const workspaceStores = {
      on: { name: 'on' },
      off: { name: 'off' },
    } as const;
    let current: keyof typeof workspaceStores;
    let lookups: number;
    let reads: sinon.SinonStub;

    beforeEach(() => {
      lookups = 0;
      setChatCompareSettingsSource(() => ({
        cacheKey: current,
        store: async () => {
          lookups += 1;
          return workspaceStores[current] as never;
        },
      }));
      reads = sinon
        .stub(platformSettings, 'getPlatformSettingsFromStore')
        .callsFake(async (store: unknown) => ({
          fileUploadMaxSizeBytes: 1,
          featureFlags: {
            ENABLE_CHAT_COMPARE: (store as { name: string }).name === 'on',
          },
        }));
    });

    it('answers for each cache key from its own store', async () => {
      current = 'on';
      expect(await ChatCompareFeature.isEnabled()).to.equal(true);
      current = 'off';
      expect(await ChatCompareFeature.isEnabled()).to.equal(false);
      current = 'on';
      expect(await ChatCompareFeature.isEnabled()).to.equal(true);
    });

    it('does not read the default settings document', async () => {
      const documentStore = sinon.stub(ChatCompareFeature, 'settingsStore');
      current = 'on';
      await ChatCompareFeature.isEnabled();
      expect(documentStore.called).to.equal(false);
    });

    it('looks the store up only when the key has no fresh answer', async () => {
      current = 'on';
      await ChatCompareFeature.isEnabled();
      await ChatCompareFeature.isEnabled();
      expect(lookups).to.equal(1);
      expect(reads.calledOnce).to.equal(true);
    });

    it('holds at most 10,000 answers, dropping the oldest first', async () => {
      let key = '';
      setChatCompareSettingsSource(() => ({
        cacheKey: key,
        store: async () => {
          lookups += 1;
          return workspaceStores.on as never;
        },
      }));
      for (let i = 0; i <= 10_000; i += 1) {
        key = `workspace-${i}`;
        await ChatCompareFeature.isEnabled();
      }
      lookups = 0;

      key = 'workspace-10000';
      await ChatCompareFeature.isEnabled();
      expect(lookups).to.equal(0);
      key = 'workspace-0';
      await ChatCompareFeature.isEnabled();
      expect(lookups).to.equal(1);
    });

    it('fails closed, without reading, while the source has no store', async () => {
      setChatCompareSettingsSource(() => null);
      expect(await ChatCompareFeature.isEnabled()).to.equal(false);
      expect(reads.called).to.equal(false);
    });

    it('fails closed when the supplied store cannot be resolved', async () => {
      setChatCompareSettingsSource(() => ({
        cacheKey: 'broken',
        store: async () => {
          throw new Error('inheritance lookup failed');
        },
      }));
      expect(await ChatCompareFeature.isEnabled()).to.equal(false);
    });

    it('stays off on the PostgreSQL document store, without consulting the source', async () => {
      process.env.DOC_STORE = 'postgres';
      current = 'on';
      expect(await ChatCompareFeature.isEnabled()).to.equal(false);
      expect(lookups).to.equal(0);
    });

    it('drops cached answers when the source is replaced', async () => {
      const sameKey = (store: object) => () => ({
        cacheKey: 'workspace',
        store: async () => store as never,
      });
      setChatCompareSettingsSource(sameKey(workspaceStores.on));
      expect(await ChatCompareFeature.isEnabled()).to.equal(true);

      setChatCompareSettingsSource(sameKey(workspaceStores.off));
      expect(await ChatCompareFeature.isEnabled()).to.equal(false);
    });

    describe('a read still running when the cache is invalidated', () => {
      const sameKey = (store: object) => () => ({
        cacheKey: 'workspace',
        store: async () => store as never,
      });

      // Starts a read on the "on" store and holds it until `release` is called.
      async function startHeldRead() {
        let release!: () => void;
        const held = new Promise<void>((resolve) => {
          release = resolve;
        });
        reads.callsFake(async (store: unknown) => {
          await held;
          return {
            fileUploadMaxSizeBytes: 1,
            featureFlags: {
              ENABLE_CHAT_COMPARE: (store as { name: string }).name === 'on',
            },
          };
        });
        setChatCompareSettingsSource(sameKey(workspaceStores.on));
        const pending = ChatCompareFeature.isEnabled();
        return { pending, release };
      }

      it('does not cache its answer once the source was replaced', async () => {
        const { pending, release } = await startHeldRead();
        setChatCompareSettingsSource(sameKey(workspaceStores.off));
        release();
        await pending;

        expect(await ChatCompareFeature.isEnabled()).to.equal(false);
      });

      it('does not cache its answer once the cache was reset', async () => {
        // The source object stays; only the store behind its key changes.
        setChatCompareSettingsSource(() => ({
          cacheKey: 'workspace',
          store: async () => workspaceStores[current] as never,
        }));
        let release!: () => void;
        const held = new Promise<void>((resolve) => {
          release = resolve;
        });
        reads.callsFake(async (store: unknown) => {
          await held;
          return {
            fileUploadMaxSizeBytes: 1,
            featureFlags: {
              ENABLE_CHAT_COMPARE: (store as { name: string }).name === 'on',
            },
          };
        });
        current = 'on';
        const pending = ChatCompareFeature.isEnabled();
        current = 'off';
        ChatCompareFeature.resetCache();
        release();
        await pending;

        expect(await ChatCompareFeature.isEnabled()).to.equal(false);
      });
    });

    it('goes back to the default settings document when called with no argument', async () => {
      current = 'on';
      expect(await ChatCompareFeature.isEnabled()).to.equal(true);

      setChatCompareSettingsSource();
      const documentStore = stubStore(true);
      reads.resolves({ fileUploadMaxSizeBytes: 1, featureFlags: {} });
      expect(await ChatCompareFeature.isEnabled()).to.equal(false);
      expect(documentStore.called).to.equal(true);
    });

    it('the route guard follows the key of the request it serves', async () => {
      const next = sinon.spy();
      current = 'off';
      await requireChatCompareEnabled({} as never, {} as never, next);
      expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError);

      next.resetHistory();
      current = 'on';
      await requireChatCompareEnabled({} as never, {} as never, next);
      expect(next.firstCall.args).to.deep.equal([]);
    });
  });
});

describe('Compare models validators', () => {
  it('branch needs a message id', () => {
    expect(
      compareBranchParamsSchema.safeParse({
        params: { conversationId: OID },
        body: { userMessageId: OID },
      }).success,
    ).to.equal(true);
    expect(
      compareBranchParamsSchema.safeParse({
        params: { conversationId: OID },
        body: {},
      }).success,
    ).to.equal(false);
    expect(
      compareBranchParamsSchema.safeParse({
        params: { conversationId: OID },
        body: { userMessageId: 'x' },
      }).success,
    ).to.equal(false);
    expect(
      compareBranchParamsSchema.safeParse({
        params: { conversationId: 'x' },
        body: { userMessageId: OID },
      }).success,
    ).to.equal(false);
  });

  it('choose takes primary or a branch id only', () => {
    const ok = (choice: unknown) =>
      compareChooseParamsSchema.safeParse({
        params: { conversationId: OID },
        body: { choice },
      }).success;
    expect(ok('primary')).to.equal(true);
    expect(ok('PRIMARY')).to.equal(true);
    expect(ok(OID)).to.equal(true);
    expect(ok('secondary')).to.equal(false);
    expect(ok(undefined)).to.equal(false);
  });

  it('compareTurn is an optional boolean on the two chat stream bodies', () => {
    const create = (extra: Record<string, unknown>) =>
      enterpriseSearchStreamCreateSchema.safeParse({
        body: { query: 'q', chatMode: 'agent', ...extra },
      }).success;
    expect(create({})).to.equal(true);
    expect(create({ compareTurn: true })).to.equal(true);
    expect(create({ compareTurn: 'yes' })).to.equal(false);
    const add = (extra: Record<string, unknown>) =>
      addMessageStreamParamsSchema.safeParse({
        params: { conversationId: OID },
        body: { query: 'q', chatMode: 'agent', ...extra },
      }).success;
    expect(add({ compareTurn: false })).to.equal(true);
    expect(add({ compareTurn: 1 })).to.equal(false);
  });
});
