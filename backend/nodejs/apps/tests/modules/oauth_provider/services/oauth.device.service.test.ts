import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { OAuthDeviceService } from '../../../../src/modules/oauth_provider/services/oauth.device.service'
import {
  OAuthDeviceCode,
  OAuthDeviceCodeStatus,
} from '../../../../src/modules/oauth_provider/schema/oauth.device_code.schema'
import { OAuthGrantType } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { DeviceGrantError, InvalidGrantError, InvalidClientError } from '../../../../src/libs/errors/oauth.errors'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { createMockLogger } from '../../../helpers/mock-logger'

describe('OAuthDeviceService', () => {
  let service: OAuthDeviceService
  let mockOAuthAppService: any
  let mockOAuthTokenService: any
  let mockScopeValidatorService: any
  let mockFirstPartyDeviceAppService: any

  const app = {
    clientId: 'cid',
    isConfidential: false,
    allowedScopes: ['user:read'],
    allowedGrantTypes: [OAuthGrantType.DEVICE_CODE],
    name: 'CLI',
  }

  beforeEach(() => {
    delete process.env.PIPESHUB_ENABLE_DEVICE_GRANT
    mockOAuthAppService = {
      getAppByClientId: sinon.stub().resolves(app),
      isGrantTypeAllowed: sinon.stub().returns(true),
      verifyClientCredentials: sinon.stub(),
    }
    mockOAuthTokenService = {
      generateTokens: sinon.stub().resolves({
        accessToken: 'at',
        tokenType: 'Bearer',
        expiresIn: 3600,
        scope: 'user:read',
        refreshToken: 'rt',
      }),
    }
    mockScopeValidatorService = {
      parseScopes: sinon.stub().returns(['user:read']),
      validateScopesForApp: sinon.stub(),
      getScopeDefinitions: sinon.stub().returns([{ name: 'user:read' }]),
    }
    mockFirstPartyDeviceAppService = {
      getOrCreate: sinon.stub().resolves('pipeshub-agent'),
    }
    service = new OAuthDeviceService(
      createMockLogger(),
      mockOAuthAppService,
      mockOAuthTokenService,
      mockScopeValidatorService,
      mockFirstPartyDeviceAppService,
    )
  })

  afterEach(() => {
    sinon.restore()
    delete process.env.PIPESHUB_ENABLE_DEVICE_GRANT
  })

  it('should create a device authorization', async () => {
    sinon.stub(OAuthDeviceCode, 'create').resolves({} as any)
    const result = await service.createAuthorization(
      'cid',
      'user:read',
      'http://localhost:3000',
    )
    expect(result.device_code).to.have.length.greaterThan(16)
    expect(result.user_code).to.match(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/)
    expect(result.verification_uri).to.equal('http://localhost:3000/oauth/device')
    expect(result.interval).to.equal(5)
  })

  it('should not reset lastPolledAt when rejecting a fast poll', async () => {
    const original = new Date(Date.now() - 1000)
    const record: any = {
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      interval: 5,
      lastPolledAt: original,
      save: sinon.stub().resolves(),
    }
    sinon.stub(OAuthDeviceCode, 'findOne').resolves(record)

    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(DeviceGrantError)
      expect((err as DeviceGrantError).oauthError).to.equal('slow_down')
    }
    expect(record.lastPolledAt).to.equal(original)
    expect(record.save.called).to.be.false
  })

  it('should return authorization_pending while the user has not approved', async () => {
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      interval: 5,
      lastPolledAt: undefined,
      save: sinon.stub().resolves(),
    } as any)

    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(DeviceGrantError)
      expect((err as DeviceGrantError).oauthError).to.equal(
        'authorization_pending',
      )
    }
  })

  it('should mint a user-identity token after approval', async () => {
    const userId = new Types.ObjectId()
    const orgId = new Types.ObjectId()
    const recordId = new Types.ObjectId()
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      _id: recordId,
      status: OAuthDeviceCodeStatus.APPROVED,
      expiresAt: new Date(Date.now() + 60_000),
      userId,
      orgId,
      scopes: ['user:read'],
      clientId: 'cid',
    } as any)
    const chainable = {
      select: sinon.stub().returnsThis(),
      lean: sinon.stub().returnsThis(),
      exec: sinon.stub().resolves(null),
    }
    sinon.stub(Users, 'findOne').returns(chainable as any)
    sinon.stub(Org, 'findOne').returns(chainable as any)
    const claim = sinon.stub(OAuthDeviceCode, 'findOneAndDelete').callsFake((query: any) => {
      expect(query.status).to.equal(OAuthDeviceCodeStatus.APPROVED)
      expect(query.expiresAt.$gt).to.be.instanceOf(Date)
      return Promise.resolve({
        _id: recordId,
        status: OAuthDeviceCodeStatus.APPROVED,
        userId,
        orgId,
        scopes: ['user:read'],
        clientId: 'cid',
      }) as any
    })

    const tokens = await service.poll('cid', undefined, 'device-code')
    expect(tokens.access_token).to.equal('at')
    expect(mockOAuthTokenService.generateTokens.firstCall.args[1]).to.equal(
      userId.toString(),
    )
    expect(mockOAuthTokenService.generateTokens.firstCall.args[1]).to.not.equal(
      null,
    )
  })

  it('should not mint tokens when a concurrent poll already claimed the code', async () => {
    const userId = new Types.ObjectId()
    const orgId = new Types.ObjectId()
    const recordId = new Types.ObjectId()
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      _id: recordId,
      status: OAuthDeviceCodeStatus.APPROVED,
      expiresAt: new Date(Date.now() + 60_000),
      userId,
      orgId,
      scopes: ['user:read'],
      clientId: 'cid',
    } as any)
    sinon.stub(OAuthDeviceCode, 'findOneAndDelete').resolves(null)

    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(InvalidGrantError)
    }
    expect(mockOAuthTokenService.generateTokens.called).to.be.false
  })

  it('should not mint tokens when the code expires between lookup and claim', async () => {
    const userId = new Types.ObjectId()
    const orgId = new Types.ObjectId()
    const recordId = new Types.ObjectId()
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      _id: recordId,
      status: OAuthDeviceCodeStatus.APPROVED,
      expiresAt: new Date(Date.now() + 60_000),
      userId,
      orgId,
      scopes: ['user:read'],
      clientId: 'cid',
    } as any)
    sinon.stub(OAuthDeviceCode, 'findOneAndDelete').resolves(null)

    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(InvalidGrantError)
    }
    expect(mockOAuthTokenService.generateTokens.called).to.be.false
  })

  it('should ensure the first-party device app before starting login', async () => {
    sinon.stub(OAuthDeviceCode, 'create').resolves({} as any)
    await service.createAuthorization(
      'pipeshub-agent',
      'user:read',
      'http://localhost:3000',
    )
    expect(mockFirstPartyDeviceAppService.getOrCreate.calledOnce).to.be.true
    expect(mockOAuthAppService.getAppByClientId.calledWith('pipeshub-agent')).to.be
      .true
  })

  it('should not ensure the first-party app for other client_ids', async () => {
    sinon.stub(OAuthDeviceCode, 'create').resolves({} as any)
    await service.createAuthorization(
      'cid',
      'user:read',
      'http://localhost:3000',
    )
    expect(mockFirstPartyDeviceAppService.getOrCreate.called).to.be.false
  })

  it('should reject first-party login when the instance has no org', async () => {
    mockFirstPartyDeviceAppService.getOrCreate.resolves(null)
    try {
      await service.createAuthorization(
        'pipeshub-agent',
        'user:read',
        'http://localhost:3000',
      )
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(InvalidClientError)
    }
    expect(mockOAuthAppService.getAppByClientId.called).to.be.false
  })
})
