import { injectable, inject } from 'inversify'
import { Response, NextFunction } from 'express'
import { Logger } from '../../../libs/services/logger.service'
import { PatService } from '../services/pat.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import { CreatePatRequest } from '../types/oauth.types'
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types'

@injectable()
export class PatController {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('PatService') private patService: PatService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * Create a new personal access token for the calling user.
   *
   * Deliberately not admin-gated — any authenticated org member can mint
   * their own PAT, unlike OAuth-app scope selection.
   */
  async createToken(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const userId = req.user!.userId
      const fullName = req.user!.fullName as string | undefined
      const data: CreatePatRequest = req.body

      const token = await this.patService.createToken(
        orgId,
        userId,
        fullName,
        data,
      )

      this.logger.info('Personal access token created via API', {
        orgId,
        userId,
        name: data.name,
      })

      res.status(201).json({
        message: 'Personal access token created successfully',
        token,
      })
    } catch (error) {
      next(error)
    }
  }

  /**
   * List the calling user's own active personal access tokens.
   */
  async listTokens(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const userId = req.user!.userId

      const tokens = await this.patService.listTokens(orgId, userId)

      res.json({ tokens })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Revoke one of the calling user's own personal access tokens.
   */
  async revokeToken(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const userId = req.user!.userId
      const tokenId = req.params.tokenId!
      const reason =
        typeof req.body?.reason === 'string' ? req.body.reason : undefined

      await this.patService.revokeToken(orgId, userId, tokenId, reason)

      this.logger.info('Personal access token revoked via API', {
        orgId,
        userId,
        tokenId,
      })

      res.json({ message: 'Personal access token revoked successfully' })
    } catch (error) {
      next(error)
    }
  }

  /**
   * List the scopes a new personal access token can be granted — the
   * org's configured MCP scope set, with human-readable labels for the
   * picker UI.
   */
  async listScopes(
    _req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const mcpScopes = await this.patService.getDefaultScopes()
      const scopes = this.scopeValidatorService.getScopeDefinitions(mcpScopes)

      res.json({ scopes })
    } catch (error) {
      next(error)
    }
  }
}
