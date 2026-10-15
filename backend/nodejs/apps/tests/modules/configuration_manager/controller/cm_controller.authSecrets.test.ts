import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths'
import { CONFIG_SECRET_PLACEHOLDER } from '../../../../src/modules/configuration_manager/utils/maskConfigSecrets'
import {
  getGoogleAuthConfig,
  getMicrosoftAuthConfig,
  setGoogleAuthConfig,
  setMicrosoftAuthConfig,
} from '../../../../src/modules/configuration_manager/controller/cm_controller'

// Sign-in provider configs follow HIDE_SECRET_CONFIG on the admin routes, reveal on
// request, stay readable on the internal route, and keep stored values when a masked
// form is saved back.

const STORED_GOOGLE = {
  clientId: 'google-client',
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
    it('masks the client id on the admin route when hidden', async () => {
      const res = response()
      await getGoogleAuthConfig(store(STORED_GOOGLE))(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.deep.equal({
        ...STORED_GOOGLE,
        clientId: CONFIG_SECRET_PLACEHOLDER,
      })
    })

    it('returns the stored values when the admin asks to see them', async () => {
      const res = response()
      await getGoogleAuthConfig(store(STORED_GOOGLE))(request({ reveal: 'true' }), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.deep.equal(STORED_GOOGLE)
    })

    it('ignores a reveal from an OAuth-app token', async () => {
      const res = response()
      const req = { ...request({ reveal: 'true' }), user: { userId: 'u1', orgId: 'o1', isOAuth: true } }
      await getGoogleAuthConfig(store(STORED_GOOGLE))(req, res, sinon.stub())
      expect(res.json.firstCall.args[0].clientId).to.equal(CONFIG_SECRET_PLACEHOLDER)
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
      const kvs = store(STORED_GOOGLE)
      const body = { ...STORED_GOOGLE, clientId: CONFIG_SECRET_PLACEHOLDER }
      await setGoogleAuthConfig(kvs)(request({}, body), response(), sinon.stub())
      expect(written(kvs)).to.include({ clientId: 'google-client' })
    })

    it('takes a newly typed secret over the stored one', async () => {
      const kvs = store(STORED_GOOGLE)
      const body = { ...STORED_GOOGLE, clientId: 'rotated' }
      await setGoogleAuthConfig(kvs)(request({}, body), response(), sinon.stub())
      expect(written(kvs)).to.include({ clientId: 'rotated' })
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

  describe('Microsoft settings before the Azure AD ones are copied', () => {
    function storeByPath(values: Record<string, Record<string, unknown>>): any {
      return {
        get: sinon.stub().callsFake(async (path: string) =>
          values[path] ? `encrypted:${JSON.stringify(values[path])}` : null,
        ),
        set: sinon.stub().resolves(),
      }
    }

    it('serves the Azure AD settings while Microsoft has none', async () => {
      const res = response()
      const kvs = storeByPath({ [configPaths.auth.azureAD]: { clientId: 'az-id', tenantId: 't' } })
      await getMicrosoftAuthConfig(kvs, false)(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.deep.equal({ clientId: 'az-id', tenantId: 't' })
    })

    it('serves the Azure AD settings when the Microsoft ones have no client id', async () => {
      const res = response()
      const kvs = storeByPath({
        [configPaths.auth.microsoft]: { tenantId: 'common' },
        [configPaths.auth.azureAD]: { clientId: 'az-id', tenantId: 't' },
      })
      await getMicrosoftAuthConfig(kvs, false)(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.include({ clientId: 'az-id' })
    })

    it('keeps saved Microsoft settings and never reads Azure AD', async () => {
      const res = response()
      const kvs = storeByPath({
        [configPaths.auth.microsoft]: { clientId: 'ms-id', tenantId: 't' },
        [configPaths.auth.azureAD]: { clientId: 'az-id', tenantId: 't' },
      })
      await getMicrosoftAuthConfig(kvs, false)(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.include({ clientId: 'ms-id' })
      expect(kvs.get.calledWith(configPaths.auth.azureAD)).to.equal(false)
    })

    it('answers an empty object when neither is set up', async () => {
      const res = response()
      await getMicrosoftAuthConfig(storeByPath({}), false)(request(), res, sinon.stub())
      expect(res.json.firstCall.args[0]).to.deep.equal({})
    })
  })
})
