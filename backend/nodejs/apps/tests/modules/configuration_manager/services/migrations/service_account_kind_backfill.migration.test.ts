import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ServiceAccountKindBackfillMigration } from '../../../../../src/modules/configuration_manager/services/migrations/service_account_kind_backfill.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { Users } from '../../../../../src/modules/user_management/schema/users.schema';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (existingFlag: string | null = null) => ({
  get: sinon.stub().callsFake((path: string) => {
    if (path === configPaths.serviceAccountKindBackfill) {
      return Promise.resolve(existingFlag);
    }
    return Promise.resolve(null);
  }),
  set: sinon.stub().resolves(),
});

const makeEvents = () => ({
  start: sinon.stub().resolves(),
  publishEvent: sinon.stub().resolves(),
});

/** Stubs the `find().select().batchSize().lean().cursor()` chain. */
const stubCursor = (accounts: unknown[]) => {
  const cursor = {
    async *[Symbol.asyncIterator]() {
      for (const a of accounts) yield a;
    },
  };
  const find = sinon.stub(Users, 'find').returns({
    select: sinon.stub().returns({
      batchSize: sinon.stub().returns({
        lean: sinon.stub().returns({ cursor: sinon.stub().returns(cursor) }),
      }),
    }),
  } as any);
  return find;
};

describe('ServiceAccountKindBackfillMigration', () => {
  const orgId = new mongoose.Types.ObjectId();
  const accountId = new mongoose.Types.ObjectId();

  const account = {
    _id: accountId,
    orgId,
    fullName: 'Nightly sync',
    email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
  };

  afterEach(() => {
    sinon.restore();
  });

  it('republishes every live service account carrying kind', async () => {
    const kv = makeKvStore(null);
    const events = makeEvents();
    stubCursor([account]);

    const result = await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      kv as any,
      events as any,
    ).run();

    expect(result.accountsFound).to.equal(1);
    expect(result.accountsRepublished).to.equal(1);
    expect(result.errored).to.equal(0);
    expect(result.skipped).to.equal(false);

    expect(events.publishEvent.calledOnce).to.equal(true);
    const published = events.publishEvent.firstCall.args[0];
    expect(published.payload.kind).to.equal('service');
    expect(published.payload.userId).to.equal(String(accountId));
    expect(published.payload.orgId).to.equal(String(orgId));
  });

  it('asks only for live service accounts', async () => {
    const find = stubCursor([]);

    await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      makeKvStore(null) as any,
      makeEvents() as any,
    ).run();

    expect(find.firstCall.args[0]).to.deep.equal({
      kind: 'service',
      isDeleted: false,
    });
  });

  it('records the flag after a clean pass', async () => {
    const kv = makeKvStore(null);
    stubCursor([account]);

    await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      kv as any,
      makeEvents() as any,
    ).run();

    expect(
      kv.set.calledWith(configPaths.serviceAccountKindBackfill, 'true'),
    ).to.equal(true);
  });

  it('does nothing once the flag is set', async () => {
    const events = makeEvents();
    const find = stubCursor([account]);

    const result = await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      makeKvStore('true') as any,
      events as any,
    ).run();

    expect(result.skipped).to.equal(true);
    expect(find.called).to.equal(false);
    expect(events.publishEvent.called).to.equal(false);
  });

  it('leaves the flag unset when an account could not be republished, so the next boot retries', async () => {
    const kv = makeKvStore(null);
    const events = makeEvents();
    events.publishEvent.rejects(new Error('broker unavailable'));
    stubCursor([account]);

    const result = await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      kv as any,
      events as any,
    ).run();

    expect(result.errored).to.equal(1);
    expect(result.accountsRepublished).to.equal(0);
    expect(kv.set.called).to.equal(false);
  });

  it('keeps going after one account fails', async () => {
    const other = { ...account, _id: new mongoose.Types.ObjectId() };
    const events = makeEvents();
    events.publishEvent.onFirstCall().rejects(new Error('transient'));
    events.publishEvent.onSecondCall().resolves();
    stubCursor([account, other]);

    const result = await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      makeKvStore(null) as any,
      events as any,
    ).run();

    expect(result.accountsFound).to.equal(2);
    expect(result.accountsRepublished).to.equal(1);
    expect(result.errored).to.equal(1);
  });

  it('still runs when the flag cannot be read', async () => {
    const kv = makeKvStore(null);
    kv.get.rejects(new Error('store unavailable'));
    const events = makeEvents();
    stubCursor([account]);

    const result = await new ServiceAccountKindBackfillMigration(
      makeLogger() as any,
      kv as any,
      events as any,
    ).run();

    expect(result.skipped).to.equal(false);
    expect(events.publishEvent.calledOnce).to.equal(true);
  });
});
