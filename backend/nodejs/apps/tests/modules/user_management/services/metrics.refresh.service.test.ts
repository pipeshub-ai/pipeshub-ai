import { expect } from 'chai';
import sinon from 'sinon';
import { startOrgMetricsRefresh } from '../../../../src/modules/user_management/services/metrics.refresh.service';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { OrgAuthConfig } from '../../../../src/modules/auth/schema/orgAuthConfiguration.schema';
import * as orgMetrics from '../../../../src/libs/services/telemetry/modules/org-metrics';
import * as userMetrics from '../../../../src/libs/services/telemetry/modules/user-metrics';
import { createMockLogger } from '../../../helpers/mock-logger';

// A very large interval so only the immediate kick-off refresh runs in tests.
const NEVER_MS = 2 ** 30;

const flushAsync = () => new Promise<void>((resolve) => setImmediate(resolve));

describe('metrics.refresh.service', () => {
  let sandbox: sinon.SinonSandbox;
  let setOrgsTotalStub: sinon.SinonStub;
  let setOrgUsersStub: sinon.SinonStub;
  let setOrgAuthMethodsStub: sinon.SinonStub;
  let mockLogger: ReturnType<typeof createMockLogger>;
  let timer: NodeJS.Timeout | undefined;

  const stubDb = (opts: {
    userAgg?: { _id: string | null; count: number }[];
    orgs?: object[];
    authConfigs?: object[];
  }) => {
    sandbox.stub(Users, 'aggregate').resolves(opts.userAgg ?? []);
    sandbox.stub(Org, 'find').returns({
      lean: sinon.stub().resolves(opts.orgs ?? []),
    } as any);
    sandbox.stub(OrgAuthConfig, 'find').returns({
      lean: sinon.stub().resolves(opts.authConfigs ?? []),
    } as any);
  };

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    setOrgsTotalStub = sandbox.stub(orgMetrics, 'setOrgsTotal');
    setOrgAuthMethodsStub = sandbox.stub(orgMetrics, 'setOrgAuthMethods');
    setOrgUsersStub = sandbox.stub(userMetrics, 'setOrgUsers');
    mockLogger = createMockLogger();
  });

  afterEach(() => {
    if (timer) {
      clearInterval(timer);
      timer = undefined;
    }
    sandbox.restore();
  });

  it('should group orgs by accountType+domain for the totals gauge', async () => {
    stubDb({
      orgs: [
        { _id: 'o1', accountType: 'business', domain: 'acme.io' },
        { _id: 'o2', accountType: 'business', domain: 'acme.io' },
        { _id: 'o3', accountType: 'individual', domain: 'solo.dev' },
      ],
    });

    timer = startOrgMetricsRefresh(mockLogger as any, NEVER_MS);
    await flushAsync();

    expect(setOrgsTotalStub.calledOnce).to.be.true;
    const rows = setOrgsTotalStub.firstCall.args[0];
    expect(rows).to.have.deep.members([
      { accountType: 'business', domain: 'acme.io', count: 2 },
      { accountType: 'individual', domain: 'solo.dev', count: 1 },
    ]);
  });

  it('should not collide groups when accountType/domain values contain separators', async () => {
    stubDb({
      orgs: [
        { _id: 'o1', accountType: 'a b', domain: 'c' },
        { _id: 'o2', accountType: 'a', domain: 'b c' },
      ],
    });

    timer = startOrgMetricsRefresh(mockLogger as any, NEVER_MS);
    await flushAsync();

    const rows = setOrgsTotalStub.firstCall.args[0];
    expect(rows).to.have.length(2);
  });

  it('should default missing accountType/domain to "unknown"', async () => {
    stubDb({
      orgs: [{ _id: 'o1', accountType: null, domain: '' }],
    });

    timer = startOrgMetricsRefresh(mockLogger as any, NEVER_MS);
    await flushAsync();

    expect(setOrgsTotalStub.firstCall.args[0]).to.deep.equal([
      { accountType: 'unknown', domain: 'unknown', count: 1 },
    ]);
  });

  it('should join user counts to org domains and default unknown orgs', async () => {
    stubDb({
      userAgg: [
        { _id: 'o1', count: 5 },
        { _id: 'orphan-org', count: 2 },
      ],
      orgs: [{ _id: 'o1', accountType: 'business', domain: 'acme.io' }],
    });

    timer = startOrgMetricsRefresh(mockLogger as any, NEVER_MS);
    await flushAsync();

    expect(setOrgUsersStub.calledOnce).to.be.true;
    expect(setOrgUsersStub.firstCall.args[0]).to.have.deep.members([
      { org: 'o1', domain: 'acme.io', count: 5 },
      { org: 'orphan-org', domain: 'unknown', count: 2 },
    ]);
  });

  it('should flatten and dedupe auth methods across auth steps', async () => {
    stubDb({
      authConfigs: [
        {
          orgId: 'o1',
          authSteps: [
            { allowedMethods: [{ type: 'password' }, { type: 'saml' }] },
            { allowedMethods: [{ type: 'saml' }, { type: '' }, { type: 42 }] },
          ],
        },
        { orgId: 'o2', authSteps: [] },
        { orgId: 'o3' },
      ],
    });

    timer = startOrgMetricsRefresh(mockLogger as any, NEVER_MS);
    await flushAsync();

    expect(setOrgAuthMethodsStub.calledOnce).to.be.true;
    expect(setOrgAuthMethodsStub.firstCall.args[0]).to.have.deep.members([
      { org: 'o1', method: 'password' },
      { org: 'o1', method: 'saml' },
    ]);
  });

  it('should warn and keep running when a DB read fails', async () => {
    sandbox.stub(Users, 'aggregate').rejects(new Error('mongo down'));
    sandbox.stub(Org, 'find').returns({
      lean: sinon.stub().resolves([]),
    } as any);
    sandbox.stub(OrgAuthConfig, 'find').returns({
      lean: sinon.stub().resolves([]),
    } as any);

    timer = startOrgMetricsRefresh(mockLogger as any, NEVER_MS);
    await flushAsync();

    expect(mockLogger.warn.called).to.be.true;
    expect(setOrgsTotalStub.called).to.be.false;
    expect(setOrgUsersStub.called).to.be.false;
  });

  it('should refresh again on the interval', async () => {
    stubDb({ orgs: [{ _id: 'o1', accountType: 'x', domain: 'y' }] });
    const clock = sandbox.useFakeTimers({
      toFake: ['setInterval'],
    });

    timer = startOrgMetricsRefresh(mockLogger as any, 1000);
    await flushAsync();
    expect(setOrgsTotalStub.callCount).to.equal(1);

    clock.tick(1000);
    await flushAsync();
    expect(setOrgsTotalStub.callCount).to.equal(2);
  });
});
