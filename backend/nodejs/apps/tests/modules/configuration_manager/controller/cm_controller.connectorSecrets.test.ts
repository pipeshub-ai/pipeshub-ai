import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config';
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor';
import * as orgUtils from '../../../../src/modules/user_management/utils/org.utils';
import { BadRequestError } from '../../../../src/libs/errors/http.errors';
import { CONFIG_SECRET_PLACEHOLDER } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets';
import {
  getAtlassianOauthConfig,
  getGoogleWorkspaceCredentials,
  getOneDriveCredentials,
  setOneDriveCredentials,
} from '../../../../src/modules/configuration_manager/controller/cm_controller';

// Connector credentials stored by an admin are masked on every admin read unless the
// server opts out with HIDE_SECRET_CONFIG=false; the internal routes the connectors use
// still read them, and a masked form saved back keeps the stored values.

const ONEDRIVE = {
  clientId: 'od-client',
  clientSecret: 'od-secret',
  tenantId: 'od-tenant',
  hasAdminConsent: true,
};
const ATLASSIAN = {
  clientId: 'at-client',
  clientSecret: 'at-secret',
  redirectUri: 'https://x/cb',
};
const SERVICE_ACCOUNT = {
  client_email: 'sa@x.iam',
  private_key: '-----BEGIN KEY-----',
  private_key_id: 'kid',
};

function store(stored: Record<string, unknown> | null): any {
  return {
    get: sinon
      .stub()
      .resolves(stored ? `encrypted:${JSON.stringify(stored)}` : null),
    set: sinon.stub().resolves(),
  };
}

function request(
  query: Record<string, unknown> = {},
  body: Record<string, unknown> = {},
  user: any = {},
): any {
  return {
    query,
    body,
    user: { userId: 'u1', orgId: 'o1', ...user },
    params: {},
  };
}

function response(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    end: sinon.stub(),
  };
  res.status.returns(res);
  res.json.returns(res);
  return res;
}

describe('connector credential secrets on the admin config routes', () => {
  const env = process.env.HIDE_SECRET_CONFIG;

  beforeEach(() => {
    sinon
      .stub(cmConfig, 'loadConfigurationManagerConfig')
      .returns({ algorithm: 'aes', secretKey: 'k' } as any);
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (val: string) => `encrypted:${val}`,
      decrypt: (val: string) => val.replace('encrypted:', ''),
    } as any);
    delete process.env.HIDE_SECRET_CONFIG;
  });

  afterEach(() => {
    sinon.restore();
    if (env === undefined) delete process.env.HIDE_SECRET_CONFIG;
    else process.env.HIDE_SECRET_CONFIG = env;
  });

  it('masks them when the server never set HIDE_SECRET_CONFIG', async () => {
    const res = response();
    await getOneDriveCredentials(store(ONEDRIVE))(request(), res, sinon.stub());
    expect(res.json.firstCall.args[0]).to.deep.equal({
      clientId: CONFIG_SECRET_PLACEHOLDER,
      clientSecret: CONFIG_SECRET_PLACEHOLDER,
      tenantId: CONFIG_SECRET_PLACEHOLDER,
      hasAdminConsent: true,
    });
  });

  it('returns them when the server sets HIDE_SECRET_CONFIG=false', async () => {
    process.env.HIDE_SECRET_CONFIG = 'false';
    const res = response();
    await getAtlassianOauthConfig(store(ATLASSIAN))(
      request(),
      res,
      sinon.stub(),
    );
    expect(res.json.firstCall.args[0]).to.deep.equal(ATLASSIAN);
  });

  it('returns them to an admin who asks to reveal them', async () => {
    const res = response();
    await getAtlassianOauthConfig(store(ATLASSIAN))(
      request({ reveal: 'true' }),
      res,
      sinon.stub(),
    );
    expect(res.json.firstCall.args[0]).to.deep.equal(ATLASSIAN);
  });

  it('keeps them masked for an OAuth-app or personal token that asks to reveal', async () => {
    const res = response();
    await getAtlassianOauthConfig(store(ATLASSIAN))(
      request({ reveal: 'true' }, {}, { isOAuth: true }),
      res,
      sinon.stub(),
    );
    expect(res.json.firstCall.args[0].clientSecret).to.equal(
      CONFIG_SECRET_PLACEHOLDER,
    );
  });

  it('returns them on the internal route the connector reads', async () => {
    const res = response();
    await getAtlassianOauthConfig(store(ATLASSIAN), false)(
      request(),
      res,
      sinon.stub(),
    );
    expect(res.json.firstCall.args[0]).to.deep.equal(ATLASSIAN);
  });

  it("masks a business workspace's service-account key, and the internal route still reads it", async () => {
    sinon
      .stub(orgUtils, 'findActiveOrgById')
      .resolves({ accountType: 'business' } as any);
    const admin = response();
    await getGoogleWorkspaceCredentials(store(SERVICE_ACCOUNT), 'u1', 'o1')(
      request(),
      admin,
      sinon.stub(),
    );
    expect(admin.json.firstCall.args[0]).to.deep.equal({
      client_email: 'sa@x.iam',
      private_key: CONFIG_SECRET_PLACEHOLDER,
      private_key_id: CONFIG_SECRET_PLACEHOLDER,
    });

    const internal = response();
    await getGoogleWorkspaceCredentials(
      store(SERVICE_ACCOUNT),
      'u1',
      'o1',
      false,
    )(request(), internal, sinon.stub());
    expect(internal.json.firstCall.args[0]).to.deep.equal(SERVICE_ACCOUNT);
  });

  it('keeps the stored secrets when a masked form is saved back', async () => {
    const kvs = store(ONEDRIVE);
    const body = {
      ...ONEDRIVE,
      clientSecret: CONFIG_SECRET_PLACEHOLDER,
      tenantId: CONFIG_SECRET_PLACEHOLDER,
    };
    await setOneDriveCredentials(kvs)(
      request({}, body),
      response(),
      sinon.stub(),
    );
    expect(
      JSON.parse(String(kvs.set.firstCall.args[1]).replace('encrypted:', '')),
    ).to.deep.equal(ONEDRIVE);
  });

  it('refuses the placeholder when there is nothing stored to keep', async () => {
    const kvs = store(null);
    const next = sinon.stub();
    await setOneDriveCredentials(kvs)(
      request({}, { ...ONEDRIVE, clientSecret: CONFIG_SECRET_PLACEHOLDER }),
      response(),
      next,
    );
    expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError);
    expect(kvs.set.called).to.equal(false);
  });
});
