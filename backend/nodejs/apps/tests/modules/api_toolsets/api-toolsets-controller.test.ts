import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as connectorUtils from '../../../src/modules/tokens_manager/utils/connector.utils';
import {
  getApiToolsetOAuthUrl,
  getApiToolsetSharedOAuthUrl,
} from '../../../src/modules/api_toolsets/controller/api_toolsets.controller';

describe('API toolsets gateway: starting a sign-in', () => {
  afterEach(() => sinon.restore());

  const call = async (handler: typeof getApiToolsetOAuthUrl, query: Record<string, string>) => {
    const execute = sinon.stub(connectorUtils, 'executeConnectorCommand').resolves({ statusCode: 200, data: {} } as never);
    sinon.stub(connectorUtils, 'handleConnectorResponse');
    const req = { user: { userId: 'u1', orgId: 'o1' }, params: { instanceId: 'a'.repeat(32) }, query, headers: {} };
    await handler({ connectorBackend: 'http://connector:8088' } as never)(req as never, {} as never, sinon.stub());
    return execute.firstCall.args[0] as string;
  };

  it('passes `desktop` on, so a sign-in from the desktop app is marked', async () => {
    const url = await call(getApiToolsetOAuthUrl, { desktop: 'true', other: 'x' });
    expect(url).to.equal(`http://connector:8088/api/v1/api-toolsets/instances/${'a'.repeat(32)}/oauth/authorize?desktop=true`);
  });

  it('sends no query when none is given, for the shared credential too', async () => {
    const url = await call(getApiToolsetSharedOAuthUrl, {});
    expect(url).to.equal(`http://connector:8088/api/v1/api-toolsets/instances/${'a'.repeat(32)}/shared-credentials/oauth/authorize`);
  });
});
