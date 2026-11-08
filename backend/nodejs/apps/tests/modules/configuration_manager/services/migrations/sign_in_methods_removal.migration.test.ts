import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { SignInMethodsRemovalMigration } from '../../../../../src/modules/configuration_manager/services/migrations/sign_in_methods_removal.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { OrgAuthConfig } from '../../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { useCommunitySignInLists } from '../../../../helpers/sign-in-lists';

const makeLogger = () => ({
  info: sinon.stub(),
  error: sinon.stub(),
  debug: sinon.stub(),
  warn: sinon.stub(),
});

const makeKvStore = (values: Record<string, string | null> = {}) => ({
  get: sinon
    .stub()
    .callsFake((path: string) => Promise.resolve(values[path] ?? null)),
  set: sinon.stub().resolves(),
});

const stubFind = (policies: unknown[]) =>
  sinon.stub(OrgAuthConfig, 'find').returns({
    select: sinon.stub().returns({ lean: sinon.stub().resolves(policies) }),
  } as any);

const policy = (types: string[], removedSignInMethods?: string[]) => ({
  _id: new mongoose.Types.ObjectId(),
  orgId: new mongoose.Types.ObjectId(),
  authSteps: [{ order: 1, allowedMethods: types.map((type) => ({ type })) }],
  ...(removedSignInMethods ? { removedSignInMethods } : {}),
});

const run = (kv: ReturnType<typeof makeKvStore>, logger = makeLogger()) =>
  new SignInMethodsRemovalMigration(logger as any, kv as any).run();

describe('SignInMethodsRemovalMigration', () => {
  useCommunitySignInLists();

  afterEach(() => {
    sinon.restore();
  });

  it('skips when the migration already ran', async () => {
    const kv = makeKvStore({ [configPaths.signInMethodsRemovalMigration]: 'true' });
    const find = stubFind([]);

    const result = await run(kv);

    expect(result).to.deep.equal({ policiesUpdated: 0, azureAdConfigCopied: false, errored: 0 });
    expect(find.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('still runs when the flag cannot be read', async () => {
    const kv = makeKvStore();
    kv.get.withArgs(configPaths.signInMethodsRemovalMigration).rejects(new Error('kv down'));
    stubFind([]);
    const logger = makeLogger();

    const result = await run(kv, logger);

    expect(result.errored).to.equal(0);
    expect(logger.warn.calledOnce).to.equal(true);
    expect(kv.set.calledWith(configPaths.signInMethodsRemovalMigration, 'true')).to.equal(true);
  });

  it('logs a non-Error flag failure as unknown', async () => {
    const kv = makeKvStore();
    kv.get.withArgs(configPaths.signInMethodsRemovalMigration).callsFake(() => Promise.reject('nope'));
    stubFind([]);
    const logger = makeLogger();

    await run(kv, logger);

    expect(logger.warn.firstCall.args[1]).to.deep.equal({ error: 'Unknown error' });
  });

  it('rewrites only the policies that change and records what was removed', async () => {
    const saml = policy(['samlSso'], ['oauth']);
    const untouched = policy(['password', 'google']);
    const renamed = policy(['password', 'azureAd']);
    stubFind([saml, untouched, renamed]);
    const updateOne = sinon.stub(OrgAuthConfig, 'updateOne').resolves({} as any);
    const kv = makeKvStore();

    const result = await run(kv);

    expect(result).to.deep.equal({ policiesUpdated: 2, azureAdConfigCopied: false, errored: 0 });
    expect(updateOne.callCount).to.equal(2);
    expect(updateOne.firstCall.args).to.deep.equal([
      { _id: saml._id },
      {
        $set: {
          authSteps: [{ order: 1, allowedMethods: [{ type: 'password' }] }],
          removedSignInMethods: ['oauth', 'samlSso'],
        },
      },
    ]);
    // A rename alone records nothing as removed.
    expect(updateOne.secondCall.args).to.deep.equal([
      { _id: renamed._id },
      {
        $set: {
          authSteps: [
            { order: 1, allowedMethods: [{ type: 'password' }, { type: 'microsoft' }] },
          ],
        },
      },
    ]);
    expect(kv.set.calledWith(configPaths.signInMethodsRemovalMigration, 'true')).to.equal(true);
  });

  it('keeps going after one policy fails and does not mark the migration done', async () => {
    const first = policy(['samlSso']);
    const second = policy(['oauth', 'google']);
    stubFind([first, second]);
    const updateOne = sinon.stub(OrgAuthConfig, 'updateOne') as sinon.SinonStub;
    updateOne.onFirstCall().rejects(new Error('write failed'));
    updateOne.onSecondCall().resolves({} as any);
    const kv = makeKvStore();
    const logger = makeLogger();

    const result = await run(kv, logger);

    expect(result.errored).to.equal(1);
    expect(result.policiesUpdated).to.equal(1);
    expect(updateOne.callCount).to.equal(2);
    expect(kv.set.calledWith(configPaths.signInMethodsRemovalMigration)).to.equal(false);
    expect(logger.error.firstCall.args[1]).to.deep.equal({
      orgId: String(first.orgId),
      error: 'write failed',
    });
  });

  it('logs a non-Error policy write failure as unknown', async () => {
    stubFind([policy(['samlSso'])]);
    (sinon.stub(OrgAuthConfig, 'updateOne') as sinon.SinonStub).callsFake(() => Promise.reject('nope'));
    const logger = makeLogger();

    await run(makeKvStore(), logger);

    expect(logger.error.firstCall.args[1].error).to.equal('Unknown error');
  });

  it('counts a failure to read the policies and leaves the flag unset', async () => {
    sinon.stub(OrgAuthConfig, 'find').returns({
      select: sinon.stub().returns({ lean: sinon.stub().rejects(new Error('db down')) }),
    } as any);
    const kv = makeKvStore();
    const logger = makeLogger();

    const result = await run(kv, logger);

    expect(result.errored).to.equal(1);
    expect(kv.set.called).to.equal(false);
    expect(logger.error.firstCall.args[1]).to.deep.equal({ error: 'db down' });
  });

  it('logs a non-Error policy read failure as unknown', async () => {
    sinon.stub(OrgAuthConfig, 'find').returns({
      select: sinon.stub().returns({ lean: sinon.stub().callsFake(() => Promise.reject('nope')) }),
    } as any);
    const logger = makeLogger();

    await run(makeKvStore(), logger);

    expect(logger.error.firstCall.args[1]).to.deep.equal({ error: 'Unknown error' });
  });

  it('reads only policies that are not deleted', async () => {
    const find = stubFind([]);

    await run(makeKvStore());

    expect((find.firstCall.args as unknown[])[0]).to.deep.equal({ isDeleted: { $ne: true } });
  });

  it('counts a failure to record the migration as done', async () => {
    stubFind([]);
    const kv = makeKvStore();
    kv.set.withArgs(configPaths.signInMethodsRemovalMigration).rejects(new Error('kv down'));
    const logger = makeLogger();

    const result = await run(kv, logger);

    expect(result.errored).to.equal(1);
    expect(logger.error.firstCall.args[1]).to.deep.equal({ error: 'kv down' });
  });

  it('logs a non-Error flag write failure as unknown', async () => {
    stubFind([]);
    const kv = makeKvStore();
    kv.set.withArgs(configPaths.signInMethodsRemovalMigration).callsFake(() => Promise.reject('nope'));
    const logger = makeLogger();

    await run(kv, logger);

    expect(logger.error.firstCall.args[1]).to.deep.equal({ error: 'Unknown error' });
  });

  describe('Azure AD settings', () => {
    it('copies them to Microsoft when Microsoft has none', async () => {
      stubFind([]);
      const kv = makeKvStore({ [configPaths.auth.azureAD]: 'encrypted-azure' });

      const result = await run(kv);

      expect(result.azureAdConfigCopied).to.equal(true);
      expect(kv.set.calledWith(configPaths.auth.microsoft, 'encrypted-azure')).to.equal(true);
    });

    it('leaves existing Microsoft settings alone', async () => {
      stubFind([]);
      const kv = makeKvStore({
        [configPaths.auth.azureAD]: 'encrypted-azure',
        [configPaths.auth.microsoft]: 'encrypted-microsoft',
      });

      const result = await run(kv);

      expect(result.azureAdConfigCopied).to.equal(false);
      expect(kv.set.calledWith(configPaths.auth.microsoft)).to.equal(false);
    });

    it('does nothing when there are no Azure AD settings', async () => {
      stubFind([]);
      const kv = makeKvStore();

      const result = await run(kv);

      expect(result.azureAdConfigCopied).to.equal(false);
      expect(kv.get.calledWith(configPaths.auth.microsoft)).to.equal(false);
    });

    it('counts a failed copy and leaves the flag unset', async () => {
      stubFind([]);
      const kv = makeKvStore({ [configPaths.auth.azureAD]: 'encrypted-azure' });
      kv.set.withArgs(configPaths.auth.microsoft).rejects(new Error('kv down'));
      const logger = makeLogger();

      const result = await run(kv, logger);

      expect(result.errored).to.equal(1);
      expect(result.azureAdConfigCopied).to.equal(false);
      expect(kv.set.calledWith(configPaths.signInMethodsRemovalMigration)).to.equal(false);
      expect(logger.error.firstCall.args[1]).to.deep.equal({ error: 'kv down' });
    });

    it('leaves every policy as it was when the copy failed, so sign-in still finds Azure AD', async () => {
      const find = stubFind([policy(['azureAd'])]);
      const updateOne = sinon.stub(OrgAuthConfig, 'updateOne').resolves({} as any);
      const kv = makeKvStore({ [configPaths.auth.azureAD]: 'encrypted-azure' });
      kv.set.withArgs(configPaths.auth.microsoft).rejects(new Error('kv down'));

      const result = await run(kv);

      expect(result.policiesUpdated).to.equal(0);
      expect(find.called).to.equal(false);
      expect(updateOne.called).to.equal(false);
    });

    it('logs a non-Error copy failure as unknown', async () => {
      stubFind([]);
      const kv = makeKvStore({ [configPaths.auth.azureAD]: 'encrypted-azure' });
      kv.set.withArgs(configPaths.auth.microsoft).callsFake(() => Promise.reject('nope'));
      const logger = makeLogger();

      await run(kv, logger);

      expect(logger.error.firstCall.args[1]).to.deep.equal({ error: 'Unknown error' });
    });
  });
});
