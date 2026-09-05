import { injectable, inject } from 'inversify'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import {
  DeviceGrantError,
  InvalidClientError,
  InvalidGrantError,
  UnsupportedGrantTypeError,
} from '../../../libs/errors/oauth.errors'
import { NotFoundError, BadRequestError } from '../../../libs/errors/http.errors'
import {
  OAuthDeviceCode,
  OAuthDeviceCodeStatus,
} from '../schema/oauth.device_code.schema'
import { OAuthGrantType } from '../schema/oauth.app.schema'
import { OAuthAppService } from './oauth.app.service'
import { OAuthTokenService } from './oauth_token.service'
import { ScopeValidatorService } from './scope.validator.service'
import { Users } from '../../user_management/schema/users.schema'
import { Org } from '../../user_management/schema/org.schema'
import { TokenResponse, ConsentData } from '../types/oauth.types'

const DEVICE_CODE_BYTES = 32
const USER_CODE_LENGTH = 8
const DEVICE_TTL_SECONDS = 600
const POLL_INTERVAL_SECONDS = 5
const USER_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTVWXYZ23456789'

export interface DeviceAuthorizationResponse {
  device_code: string
  user_code: string
  verification_uri: string
  verification_uri_complete: string
  expires_in: number
  interval: number
}

@injectable()
export class OAuthDeviceService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthAppService') private oauthAppService: OAuthAppService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  async createAuthorization(
    clientId: string,
    scope: string | undefined,
    frontendUrl: string,
  ): Promise<DeviceAuthorizationResponse> {
    if (process.env.PIPESHUB_ENABLE_DEVICE_GRANT === 'false') {
      throw new UnsupportedGrantTypeError('device grant is disabled')
    }

    const app = await this.oauthAppService.getAppByClientId(clientId)
    if (!this.oauthAppService.isGrantTypeAllowed(app, OAuthGrantType.DEVICE_CODE)) {
      throw new UnsupportedGrantTypeError(
        'device_code grant not allowed for this app',
      )
    }

    const requested = scope
      ? this.scopeValidatorService.parseScopes(scope)
      : app.allowedScopes
    this.scopeValidatorService.validateScopesForApp(requested, app.allowedScopes)

    const deviceCode = crypto.randomBytes(DEVICE_CODE_BYTES).toString('hex')
    const userCode = this.generateUserCode()
    const expiresAt = new Date(Date.now() + DEVICE_TTL_SECONDS * 1000)

    await OAuthDeviceCode.create({
      deviceCodeHash: this.hashDeviceCode(deviceCode),
      userCode,
      clientId,
      scopes: requested,
      status: OAuthDeviceCodeStatus.PENDING,
      interval: POLL_INTERVAL_SECONDS,
      expiresAt,
    })

    const verificationUri = new URL('/oauth/device', frontendUrl).toString()
    const displayCode = `${userCode.slice(0, 4)}-${userCode.slice(4)}`

    this.logger.info('Device authorization created', { clientId, userCode })

    return {
      device_code: deviceCode,
      user_code: displayCode,
      verification_uri: verificationUri,
      verification_uri_complete: `${verificationUri}?user_code=${displayCode}`,
      expires_in: DEVICE_TTL_SECONDS,
      interval: POLL_INTERVAL_SECONDS,
    }
  }

  async getConsentData(userCodeRaw: string): Promise<ConsentData> {
    const record = await this.findPendingByUserCode(userCodeRaw)
    const app = await this.oauthAppService.getAppByClientId(record.clientId)
    const scopeDefinitions = this.scopeValidatorService.getScopeDefinitions(
      record.scopes,
    )
    return {
      app: {
        name: app.name,
        description: app.description,
        logoUrl: app.logoUrl,
        homepageUrl: app.homepageUrl,
        privacyPolicyUrl: app.privacyPolicyUrl,
      },
      scopes: scopeDefinitions,
      user: { email: '', name: undefined },
      redirectUri: '',
      state: '',
    }
  }

  async approve(
    userCodeRaw: string,
    userId: string,
    orgId: string,
    consent: 'granted' | 'denied',
  ): Promise<void> {
    const record = await this.findPendingByUserCode(userCodeRaw)
    if (consent !== 'granted') {
      record.status = OAuthDeviceCodeStatus.DENIED
      await record.save()
      this.logger.info('Device authorization denied', {
        clientId: record.clientId,
        userId,
      })
      return
    }
    record.status = OAuthDeviceCodeStatus.APPROVED
    record.userId = new Types.ObjectId(userId)
    record.orgId = new Types.ObjectId(orgId)
    await record.save()
    this.logger.info('Device authorization approved', {
      clientId: record.clientId,
      userId,
      orgId,
    })
  }

  async poll(
    clientId: string,
    clientSecret: string | undefined,
    deviceCode: string,
  ): Promise<TokenResponse> {
    if (!deviceCode) {
      throw new InvalidGrantError('device_code is required')
    }

    const app = await this.oauthAppService.getAppByClientId(clientId)
    if (!this.oauthAppService.isGrantTypeAllowed(app, OAuthGrantType.DEVICE_CODE)) {
      throw new UnsupportedGrantTypeError(
        'device_code grant not allowed for this app',
      )
    }
    if (app.isConfidential) {
      if (!clientSecret) {
        throw new InvalidClientError('client_secret required for confidential clients')
      }
      await this.oauthAppService.verifyClientCredentials(clientId, clientSecret)
    }

    const record = await OAuthDeviceCode.findOne({
      deviceCodeHash: this.hashDeviceCode(deviceCode),
      clientId,
    })
    if (!record || record.expiresAt.getTime() <= Date.now()) {
      throw new DeviceGrantError('expired_token', 'device_code has expired')
    }

    if (record.status === OAuthDeviceCodeStatus.DENIED) {
      throw new DeviceGrantError('access_denied', 'the user denied the request')
    }

    if (record.status === OAuthDeviceCodeStatus.PENDING) {
      const now = Date.now()
      if (
        record.lastPolledAt &&
        now - record.lastPolledAt.getTime() < record.interval * 1000
      ) {
        record.lastPolledAt = new Date(now)
        await record.save()
        throw new DeviceGrantError(
          'slow_down',
          'polling too frequently',
        )
      }
      record.lastPolledAt = new Date(now)
      await record.save()
      throw new DeviceGrantError(
        'authorization_pending',
        'authorization is still pending',
      )
    }

    if (!record.userId || !record.orgId) {
      throw new InvalidGrantError('device authorization is incomplete')
    }

    const userId = record.userId.toString()
    const orgId = record.orgId.toString()

    let fullName: string | undefined
    let accountType: string | undefined
    const user = await Users.findOne({
      _id: record.userId,
      orgId: record.orgId,
      isDeleted: false,
    })
      .select('fullName')
      .lean()
      .exec()
    if (user) {
      fullName = user.fullName
    }
    const org = await Org.findOne({
      _id: record.orgId,
      isDeleted: false,
    })
      .select('accountType')
      .lean()
      .exec()
    if (org) {
      accountType = (org as { accountType?: string }).accountType
    }

    const tokens = await this.oauthTokenService.generateTokens(
      app,
      userId,
      orgId,
      record.scopes,
      true,
      fullName,
      accountType,
    )

    await OAuthDeviceCode.deleteOne({ _id: record._id })

    return {
      access_token: tokens.accessToken,
      token_type: tokens.tokenType,
      expires_in: tokens.expiresIn,
      refresh_token: tokens.refreshToken,
      scope: tokens.scope,
    }
  }

  private async findPendingByUserCode(userCodeRaw: string) {
    const userCode = this.normalizeUserCode(userCodeRaw)
    if (!userCode) {
      throw new BadRequestError('user_code is required')
    }
    const record = await OAuthDeviceCode.findOne({ userCode })
    if (!record) {
      throw new NotFoundError('Unknown user_code')
    }
    if (record.expiresAt.getTime() <= Date.now()) {
      throw new DeviceGrantError('expired_token', 'user_code has expired')
    }
    if (record.status !== OAuthDeviceCodeStatus.PENDING) {
      throw new BadRequestError('user_code has already been used')
    }
    return record
  }

  private generateUserCode(): string {
    const bytes = crypto.randomBytes(USER_CODE_LENGTH)
    let code = ''
    for (let i = 0; i < USER_CODE_LENGTH; i++) {
      code += USER_CODE_ALPHABET[bytes[i] % USER_CODE_ALPHABET.length]
    }
    return code
  }

  normalizeUserCode(raw: string): string {
    return raw.replace(/[^A-Za-z0-9]/g, '').toUpperCase()
  }

  private hashDeviceCode(deviceCode: string): string {
    return crypto.createHash('sha256').update(deviceCode).digest('hex')
  }
}
