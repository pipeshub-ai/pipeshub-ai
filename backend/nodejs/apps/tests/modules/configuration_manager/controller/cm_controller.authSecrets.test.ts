import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'
import { CONFIG_SECRET_PLACEHOLDER } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets'
import {
  getGoogleAuthConfig,
  getOAuthConfig,
  setGoogleAuthConfig,
  setMicrosoftAuthConfig,
  setOAuthConfig,
} from '../../../../src/modules/configuration_manager/controller/cm_controller'

// Sign-in provider configs follow HIDE_SECRET_CONFIG on the admin routes, reveal on
// request, stay readable on the internal route, and keep stored values when a masked
// form is saved back.

const STORED_OAUTH = {
  providerName: 'Okta',
  clientId: 'oauth-client',
  clientSecret: 'oauth-secret',
  enableJit: true,
}

function store(stored: Record<string, unknown> | null): any {
  return {
    get: sinon.stub().resolves(stored ? `encrypted:${JSON.stringify(stored)}` : null),
    set: sinon.stub().resolves(),
  }
}

function request(query: Record<string, unknown> = {}, body: Record<string, unknown> = {}): any {
  return { query, body, user: { userId: 'u1', orgId: 'o1' }, params: {} }
}

function response(): any {
  const res: any = { status: sinon.stub(), json: sinon.stub(), end: sinon.stub() }
  res.status.returns(res)
  res.json.returns(res)
  return res
}

function written(kvs: any): Record<string, unknown> {
  return JSON.parse(String(kvs.set.firstCall.args[1]).replace('encrypted:', ''))
}

describe('sign-in provider secrets', () => {
  const env = process.env.HIDE_SECRET_CONFIG

  beforeEach(() => {
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes', secretKey: 'k' } as any)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (val: string) => `encrypted:${val}`,
      decrypt: (val: string) => val.replace('encrypted:', ''),
    } as any)
    process.env.HIDE_SECRET_CONFIG = 'true'
  })

  afterEach(() => {
    sinon.restore()
    if (env === undefined) delete process.env.HIDE_SECRET_CONFIG
    else process.env.HIDE_SECRET_CONFIG = env
  })

  describe('reads', () => {
    it('masks client id and secret on the admin route when hidden', async () => {
      const res = response()
      await getOAuthConfig(store(STORED_OAUTH))(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.deep.equal({
        ...STORED_OAUTH,
        clientId: CONFIG_SECRET_PLACEHOLDER,
        clientSecret: CONFIG_SECRET_PLACEHOLDER,
      })
    })

    it('returns the stored values when the admin asks to see them', async () => {
      const res = response()
      await getOAuthConfig(store(STORED_OAUTH))(request({ reveal: 'true' }), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.deep.equal(STORED_OAUTH)
    })

    it('ignores a reveal from an OAuth-app token', async () => {
      const res = response()
      const req = { ...request({ reveal: 'true' }), user: { userId: 'u1', orgId: 'o1', isOAuth: true } }
      await getOAuthConfig(store(STORED_OAUTH))(req, res, sinon.stub())
      expect(res.json.firstCall.args[0].clientSecret).to.equal(CONFIG_SECRET_PLACEHOLDER)
    })

    it('gives the internal route (sign-in) the stored values while hidden', async () => {
      const res = response()
      await getGoogleAuthConfig(store({ clientId: 'g-id' }), false)(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0].clientId).to.equal('g-id')
    })

    it('shows the stored values when the flag is off', async () => {
      delete process.env.HIDE_SECRET_CONFIG
      const res = response()
      await getGoogleAuthConfig(store({ clientId: 'g-id' }))(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0].clientId).to.equal('g-id')
    })
  })

  describe('saves', () => {
    it('keeps the stored secret when the masked form is saved back', async () => {
      const kvs = store(STORED_OAUTH)
      const body = { ...STORED_OAUTH, clientId: CONFIG_SECRET_PLACEHOLDER, clientSecret: CONFIG_SECRET_PLACEHOLDER }
      await setOAuthConfig(kvs)(request({}, body), response(), sinon.stub())
      expect(written(kvs)).to.include({ clientId: 'oauth-client', clientSecret: 'oauth-secret' })
    })

    it('takes a newly typed secret over the stored one', async () => {
      const kvs = store(STORED_OAUTH)
      const body = { ...STORED_OAUTH, clientId: CONFIG_SECRET_PLACEHOLDER, clientSecret: 'rotated' }
      await setOAuthConfig(kvs)(request({}, body), response(), sinon.stub())
      expect(written(kvs)).to.include({ clientId: 'oauth-client', clientSecret: 'rotated' })
    })

    it('builds the Microsoft authority from the stored tenant, not the placeholder', async () => {
      const kvs = store({ clientId: 'ms-id', tenantId: 'ms-tenant', authority: 'x' })
      const body = { clientId: CONFIG_SECRET_PLACEHOLDER, tenantId: CONFIG_SECRET_PLACEHOLDER }
      await setMicrosoftAuthConfig(kvs)(request({}, body), response(), sinon.stub())
      expect(written(kvs)).to.include({
        clientId: 'ms-id',
        tenantId: 'ms-tenant',
        authority: 'https://login.microsoftonline.com/ms-tenant',
      })
    })

    it('refuses to store a placeholder that has nothing to restore', async () => {
      const kvs = store(null)
      const next = sinon.stub()
      await setGoogleAuthConfig(kvs)(request({}, { clientId: CONFIG_SECRET_PLACEHOLDER }), response(), next)
      expect(kvs.set.called).to.equal(false)
      expect(next.firstCall.args[0]).to.be.instanceOf(BadRequestError)
    })
  })
})
