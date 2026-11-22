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
