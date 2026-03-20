import { injectable, inject } from 'inversify'
import { Response, NextFunction } from 'express'
import { Logger } from '../../../libs/services/logger.service'
import { OAuthAppService } from '../services/oauth.app.service'
import { OAuthTokenService } from '../services/oauth_token.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import {
  CreateOAuthAppRequest,
  UpdateOAuthAppRequest,
  ListAppsQuery,
} from '../types/oauth.types'
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types'

@injectable()
export class OAuthAppController {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthAppService') private oauthAppService: OAuthAppService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * List OAuth apps for organization
   */
  async listApps(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const query: ListAppsQuery = {
        page: req.query.page ? parseInt(req.query.page as string, 10) : undefined,
        limit: req.query.limit
          ? parseInt(req.query.limit as string, 10)
          : undefined,
        status: req.query.status as string,
        search: req.query.search as string,
      }

      const result = await this.oauthAppService.listApps(orgId, query)

      res.json(result)
    } catch (error) {
      next(error)
    }
  }

  /**
   * Create new OAuth app
   */
  async createApp(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const userId = req.user!.userId
      const data: CreateOAuthAppRequest = req.body

      const app = await this.oauthAppService.createApp(orgId, userId, data)

      this.logger.info('OAuth app created', {
        appId: app.id,
        orgId,
        userId,
        name: data.name,
      })

      res.status(201).json({
        message: 'OAuth app created successfully',
        app,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Get OAuth app details
   */
  async getApp(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!

      const app = await this.oauthAppService.getAppById(appId, orgId)

      res.json(app)
    } catch (error) {
      next(error)
    }
  }

  /**
   * Update OAuth app
   */
  async updateApp(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!
      const data: UpdateOAuthAppRequest = req.body

      const app = await this.oauthAppService.updateApp(appId, orgId, data)

      this.logger.info('OAuth app updated via API', {
        appId,
        orgId,
      })

      res.json({
        message: 'OAuth app updated successfully',
        app,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Delete OAuth app
   */
  async deleteApp(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const userId = req.user!.userId
      const appId = req.params.appId!

      // Revoke all tokens first
      const app = await this.oauthAppService.getAppById(appId, orgId)
      await this.oauthTokenService.revokeAllTokensForApp(app.clientId)

      // Then delete the app
      await this.oauthAppService.deleteApp(appId, orgId, userId)

      this.logger.info('OAuth app deleted via API', {
        appId,
        orgId,
        userId,
      })

      res.json({
        message: 'OAuth app deleted successfully',
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Regenerate client secret
   */
  async regenerateSecret(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!

      const app = await this.oauthAppService.regenerateSecret(appId, orgId)

      this.logger.info('OAuth app secret regenerated via API', {
        appId,
        orgId,
      })

      res.json({
        message: 'Client secret regenerated successfully',
        clientId: app.clientId,
        clientSecret: app.clientSecret,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Suspend OAuth app
   */
  async suspendApp(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!

      const app = await this.oauthAppService.suspendApp(appId, orgId)

      this.logger.info('OAuth app suspended via API', {
        appId,
        orgId,
      })

      res.json({
        message: 'OAuth app suspended successfully',
        app,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Activate OAuth app
   */
  async activateApp(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!

      const app = await this.oauthAppService.activateApp(appId, orgId)

      this.logger.info('OAuth app activated via API', {
        appId,
        orgId,
      })

      res.json({
        message: 'OAuth app activated successfully',
        app,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * List available scopes
   */
  async listScopes(
    _req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const scopesByCategory = this.scopeValidatorService.getScopesGroupedByCategory()

      res.json({
        scopes: scopesByCategory,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * List active tokens for an app
   */
  async listAppTokens(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!

      // Verify app belongs to org
      const app = await this.oauthAppService.getAppById(appId, orgId)
      const tokens = await this.oauthTokenService.listTokensForApp(app.clientId)

      res.json({
        tokens,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Revoke all tokens for an app
   */
  async revokeAllTokens(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const appId = req.params.appId!

      // Verify app belongs to org
      const app = await this.oauthAppService.getAppById(appId, orgId)
      await this.oauthTokenService.revokeAllTokensForApp(app.clientId)

      this.logger.info('All tokens revoked for OAuth app via API', {
        appId,
        clientId: app.clientId,
        orgId,
      })

      res.json({
        message: 'All tokens revoked successfully',
      })
    } catch (error) {
      next(error)
    }
  }
}
