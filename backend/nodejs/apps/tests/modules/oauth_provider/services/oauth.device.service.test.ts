import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { OAuthDeviceService } from '../../../../src/modules/oauth_provider/services/oauth.device.service'
import {
  OAuthDeviceCode,
  OAuthDeviceCodeStatus,
} from '../../../../src/modules/oauth_provider/schema/oauth.device_code.schema'
import { OAuthGrantType } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import {
  DeviceGrantError,
  InvalidGrantError,
  InvalidClientError,
  UnsupportedGrantTypeError,
} from '../../../../src/libs/errors/oauth.errors'
import { BadRequestError, NotFoundError } from '../../../../src/libs/errors/http.errors'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { createMockLogger, MockLogger } from '../../../helpers/mock-logger'

function sha256Hex(value: string): string {
  return crypto.createHash('sha256').update(value).digest('hex')
}

describe('OAuthDeviceService', () => {
  let service: OAuthDeviceService
  let mockLogger: MockLogger
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
    mockLogger = createMockLogger()
    service = new OAuthDeviceService(
      mockLogger as any,
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
    sinon.stub(OAuthDeviceCode, 'findOneAndDelete').callsFake((query: any) => {
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
    expect(mockOAuthTokenService.generateTokens.firstCall.args[2]).to.equal(
      orgId.toString(),
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

  it('should refuse create when PIPESHUB_ENABLE_DEVICE_GRANT is false', async () => {
    process.env.PIPESHUB_ENABLE_DEVICE_GRANT = 'false'
    try {
      await service.createAuthorization(
        'cid',
        'user:read',
        'http://localhost:3000',
      )
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(UnsupportedGrantTypeError)
    }
    expect(mockOAuthAppService.getAppByClientId.called).to.be.false
  })

  it('should store a SHA-256 of device_code and a hyphen-free userCode', async () => {
    const create = sinon.stub(OAuthDeviceCode, 'create').resolves({} as any)
    const result = await service.createAuthorization(
      'cid',
      'user:read',
      'http://localhost:3000',
    )
    const stored = create.firstCall.args[0] as {
      deviceCodeHash: string
      userCode: string
    }
    expect(stored.deviceCodeHash).to.equal(sha256Hex(result.device_code))
    expect(stored.deviceCodeHash).to.not.equal(result.device_code)
    expect(stored.userCode).to.not.include('-')
    expect(result.user_code).to.match(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/)
    expect(stored.userCode).to.equal(result.user_code.replace('-', ''))
  })

  it('should not log device_code or user_code', async () => {
    sinon.stub(OAuthDeviceCode, 'create').resolves({} as any)
    const result = await service.createAuthorization(
      'cid',
      'user:read',
      'http://localhost:3000',
    )
    const logged = JSON.stringify(mockLogger.info.args)
    expect(logged).to.not.include(result.device_code)
    expect(logged).to.not.include(result.user_code)
  })

  it('should look up polls by hashed device_code, not plaintext', async () => {
    const plaintext = 'plaintext-device-code'
    const findOne = sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      interval: 5,
      lastPolledAt: undefined,
      save: sinon.stub().resolves(),
    } as any)
    try {
      await service.poll('cid', undefined, plaintext)
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(DeviceGrantError)
    }
    expect(findOne.firstCall.args[0].deviceCodeHash).to.deep.equal({
      $eq: sha256Hex(plaintext),
    })
    expect(findOne.firstCall.args[0].deviceCodeHash.$eq).to.not.equal(plaintext)
  })

  it('should reject a missing device_code before lookup', async () => {
    try {
      await service.poll('cid', undefined, '')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(InvalidGrantError)
    }
    expect(mockOAuthAppService.getAppByClientId.called).to.be.false
  })

  it('should return expired_token when the device_code is unknown or expired', async () => {
    sinon.stub(OAuthDeviceCode, 'findOne').resolves(null)
    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(DeviceGrantError)
      expect((err as DeviceGrantError).oauthError).to.equal('expired_token')
    }
  })

  it('should return access_denied when the user denied the request', async () => {
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      status: OAuthDeviceCodeStatus.DENIED,
      expiresAt: new Date(Date.now() + 60_000),
    } as any)
    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(DeviceGrantError)
      expect((err as DeviceGrantError).oauthError).to.equal('access_denied')
    }
    expect(mockOAuthTokenService.generateTokens.called).to.be.false
  })

  it('should require client_secret for confidential clients', async () => {
    mockOAuthAppService.getAppByClientId.resolves({
      ...app,
      isConfidential: true,
    })
    try {
      await service.poll('cid', undefined, 'device-code')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(InvalidClientError)
    }
    expect(mockOAuthAppService.verifyClientCredentials.called).to.be.false
  })

  it('should poll a public client without a secret', async () => {
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
      expect((err as DeviceGrantError).oauthError).to.equal(
        'authorization_pending',
      )
    }
    expect(mockOAuthAppService.verifyClientCredentials.called).to.be.false
  })

  it('should set userId and orgId on grant, and not on deny', async () => {
    const userId = new Types.ObjectId().toString()
    const orgId = new Types.ObjectId().toString()
    const granted: any = {
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      clientId: 'cid',
      save: sinon.stub().resolves(),
    }
    const denied: any = {
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      clientId: 'cid',
      save: sinon.stub().resolves(),
    }
    const findOne = sinon.stub(OAuthDeviceCode, 'findOne')
    findOne.onFirstCall().resolves(granted)
    findOne.onSecondCall().resolves(denied)

    await service.approve('ABCD-EFGH', userId, orgId, 'granted')
    expect(granted.status).to.equal(OAuthDeviceCodeStatus.APPROVED)
    expect(granted.userId.toString()).to.equal(userId)
    expect(granted.orgId.toString()).to.equal(orgId)

    await service.approve('ABCD-EFGH', userId, orgId, 'denied')
    expect(denied.status).to.equal(OAuthDeviceCodeStatus.DENIED)
    expect(denied.userId).to.equal(undefined)
    expect(mockOAuthTokenService.generateTokens.called).to.be.false
  })

  it('should surface isDynamic on consent data', async () => {
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      clientId: 'cid',
      scopes: ['user:read'],
    } as any)
    mockOAuthAppService.getAppByClientId.resolves({
      ...app,
      isDynamic: true,
      description: 'dyn',
    })
    const data = await service.getConsentData('ABCD-EFGH')
    expect(data.app.isDynamic).to.equal(true)
  })

  it('should omit isDynamic when the app is first-party', async () => {
    sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      clientId: 'cid',
      scopes: ['user:read'],
    } as any)
    mockOAuthAppService.getAppByClientId.resolves({ ...app, isDynamic: false })
    const data = await service.getConsentData('ABCD-EFGH')
    expect(data.app.isDynamic).to.equal(false)
  })

  it('should normalize hyphenated user_code before lookup', async () => {
    const findOne = sinon.stub(OAuthDeviceCode, 'findOne').resolves({
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() + 60_000),
      clientId: 'cid',
      scopes: ['user:read'],
    } as any)
    mockOAuthAppService.getAppByClientId.resolves(app)
    await service.getConsentData('ab-cd-efgh')
    expect(findOne.firstCall.args[0]).to.deep.equal({ userCode: 'ABCDEFGH' })
  })

  it('should reject unknown, expired, and already-used user_code', async () => {
    const findOne = sinon.stub(OAuthDeviceCode, 'findOne')
    findOne.onFirstCall().resolves(null)
    try {
      await service.getConsentData('ABCD-EFGH')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(NotFoundError)
    }

    findOne.onSecondCall().resolves({
      status: OAuthDeviceCodeStatus.PENDING,
      expiresAt: new Date(Date.now() - 1000),
    } as any)
    try {
      await service.getConsentData('ABCD-EFGH')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(DeviceGrantError)
      expect((err as DeviceGrantError).oauthError).to.equal('expired_token')
    }

    findOne.onThirdCall().resolves({
      status: OAuthDeviceCodeStatus.APPROVED,
      expiresAt: new Date(Date.now() + 60_000),
    } as any)
    try {
      await service.getConsentData('ABCD-EFGH')
      expect.fail('should have thrown')
    } catch (err) {
      expect(err).to.be.instanceOf(BadRequestError)
    }
  })

  it('should strip hyphens in normalizeUserCode', () => {
    expect(service.normalizeUserCode('ab-cd-efgh')).to.equal('ABCDEFGH')
    expect(service.normalizeUserCode('ABCD EFGH')).to.equal('ABCDEFGH')
  })
})
