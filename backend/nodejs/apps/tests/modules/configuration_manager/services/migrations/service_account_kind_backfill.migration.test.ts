import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import crypto from 'crypto';
import mongoose from 'mongoose';
import {
  ServiceAccountKindBackfillMigration,
  SERVICE_ACCOUNT_KIND_STATE_VERSION,
  ServiceAccountKindState,
} from '../../../../../src/modules/configuration_manager/services/migrations/service_account_kind_backfill.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { EncryptionService } from '../../../../../src/libs/encryptor/encryptor';
import { Users } from '../../../../../src/modules/user_management/schema/users.schema';

const algorithm = 'aes-256-gcm';
const secretKey = crypto.randomBytes(32).toString('hex');

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

/**
 * How the connector service stores a value: the ciphertext, JSON-encoded. Its
 * store JSON-decodes the bytes before handing them to the decrypter.
 */
const seal = (state: ServiceAccountKindState): string =>
  JSON.stringify(
    EncryptionService.getInstance(algorithm, secretKey).encrypt(
      JSON.stringify(state),
    ),
  );

const unseal = (raw: string): ServiceAccountKindState =>
  JSON.parse(
    EncryptionService.getInstance(algorithm, secretKey).decrypt(JSON.parse(raw)),
  );

const makeKvStore = (stored: string | null = null) => ({
  get: sinon.stub().callsFake((path: string) =>
    Promise.resolve(path === configPaths.serviceAccountKindBackfill ? stored : null),
  ),
  set: sinon.stub().resolves(),
});

/** Stubs the `find().select().batchSize().lean().cursor()` chain. */
const stubCursor = (accounts: unknown[]) =>
  sinon.stub(Users, 'find').returns({
    select: sinon.stub().returns({
      batchSize: sinon.stub().returns({
        lean: sinon.stub().returns({
          cursor: sinon.stub().returns({
            async *[Symbol.asyncIterator]() {
              for (const a of accounts) yield a;
            },
          }),
        }),
      }),
    }),
  } as any);

const run = (kvStore: any) =>
  new ServiceAccountKindBackfillMigration(
    makeLogger() as any,
    kvStore as any,
    algorithm,
    secretKey,
  ).run();

describe('ServiceAccountKindBackfillMigration', () => {
  const first = new mongoose.Types.ObjectId();
  const second = new mongoose.Types.ObjectId();
  const accounts = [{ _id: first }, { _id: second }];

  beforeEach(() => {
    (EncryptionService as any).instance = undefined;
  });

  afterEach(() => {
    sinon.restore();
    (EncryptionService as any).instance = undefined;
  });

  it('stages the id of every live service account', async () => {
    stubCursor(accounts);
    const kvStore = makeKvStore();

    const result = await run(kvStore);

    expect(result.accountsFound).to.equal(2);
    expect(result.accountsStaged).to.equal(2);
    expect(result.skipped).to.equal(false);
    expect(kvStore.set.calledOnce).to.equal(true);
    expect(kvStore.set.firstCall.args[0]).to.equal(
      configPaths.serviceAccountKindBackfill,
    );
  });

  it('stages only live accounts of kind service', async () => {
    const find = stubCursor(accounts);
    await run(makeKvStore());

    expect(find.firstCall.args[0]).to.deep.equal({
      kind: 'service',
      isDeleted: false,
    });
  });

  it('writes something the connector service can read back', async () => {
    stubCursor(accounts);
    const kvStore = makeKvStore();

    await run(kvStore);

    const state = unseal(kvStore.set.firstCall.args[1]);
    expect(state.version).to.equal(SERVICE_ACCOUNT_KIND_STATE_VERSION);
    expect(state.userIds).to.deep.equal([String(first), String(second)]);
    expect(state.stagedAt).to.be.a('number');
  });

  it('never records the work as done itself', async () => {
    stubCursor(accounts);
    const kvStore = makeKvStore();

    await run(kvStore);

    expect(unseal(kvStore.set.firstCall.args[1]).done).to.equal(undefined);
  });

  it('does nothing once the connector service has reported done', async () => {
    const find = stubCursor(accounts);
    const kvStore = makeKvStore(
      seal({ version: SERVICE_ACCOUNT_KIND_STATE_VERSION, done: true }),
    );

    const result = await run(kvStore);

    expect(result.skipped).to.equal(true);
    expect(find.called).to.equal(false);
    expect(kvStore.set.called).to.equal(false);
  });

  it('does not rewrite a staged list that still matches', async () => {
    stubCursor(accounts);
    const kvStore = makeKvStore(
      seal({
        version: SERVICE_ACCOUNT_KIND_STATE_VERSION,
        userIds: [String(second), String(first)],
      }),
    );

    const result = await run(kvStore);

    expect(result.accountsStaged).to.equal(0);
    expect(result.skipped).to.equal(true);
    expect(kvStore.set.called).to.equal(false);
  });

  it('stages again once an account has been added', async () => {
    stubCursor(accounts);
    const kvStore = makeKvStore(
      seal({
        version: SERVICE_ACCOUNT_KIND_STATE_VERSION,
        userIds: [String(first)],
      }),
    );

    const result = await run(kvStore);

    expect(result.accountsStaged).to.equal(2);
    expect(unseal(kvStore.set.firstCall.args[1]).userIds).to.have.members([
      String(first),
      String(second),
    ]);
  });

  it('stages anyway when the stored state cannot be read', async () => {
    stubCursor(accounts);
    const kvStore = makeKvStore('not something we wrote');

    const result = await run(kvStore);

    expect(result.accountsStaged).to.equal(2);
    expect(kvStore.set.calledOnce).to.equal(true);
  });

  it('writes nothing when there are no service accounts', async () => {
    stubCursor([]);
    const kvStore = makeKvStore();

    const result = await run(kvStore);

    expect(result.accountsFound).to.equal(0);
    expect(result.skipped).to.equal(true);
    expect(kvStore.set.called).to.equal(false);
  });

  it('stages nothing rather than half a list when there are too many', async () => {
    const tooMany = Array.from({ length: 10_001 }, () => ({
      _id: new mongoose.Types.ObjectId(),
    }));
    stubCursor(tooMany);
    const kvStore = makeKvStore();

    const result = await run(kvStore);

    expect(result.accountsStaged).to.equal(0);
    expect(kvStore.set.called).to.equal(false);
  });
});
