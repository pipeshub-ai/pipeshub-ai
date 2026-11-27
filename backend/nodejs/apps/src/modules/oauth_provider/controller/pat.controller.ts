import { injectable, inject } from 'inversify'
import { Response, NextFunction } from 'express'
import { DEFAULT_EXPIRY_DAYS, PatService } from '../services/pat.service'
import { ScopeValidatorService } from '../services/scope.validator.service'
import { CreatePatRequest, UpdatePatKnowledgeLimitBody } from '../types/oauth.types'
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types'
import { currentOrgContext } from '../../../libs/context/orgContext';
import { knowledgeLimitSize } from '../types/knowledge_limit';
import { emitPlatformEvent } from '../../../libs/platform-events'
import {
  emailDomain,
  requestClient,
} from '../../../libs/utils/request-client.utils'

@injectable()
export class PatController {
  constructor(
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

      // Activation signal: a token is the first step of every programmatic
      // path (MCP clients, SDKs). Scopes are counted, not listed.
      const email = req.user?.email as string | undefined
      emitPlatformEvent('personal_access_token.created', {
        orgId,
        userId,
        emailDomain: emailDomain(email),
        scopeCount: token.scopes.length,
        // The lifetime the token was actually minted with.
        expiryDays: data.expiryDays ?? DEFAULT_EXPIRY_DAYS,
        ...requestClient(req.headers),
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

      res.json({ message: 'Personal access token revoked successfully' })
    } catch (error) {
      next(error)
    }
  }

  /**
   * Change what one of the caller's own personal access tokens may see.
   * The token keeps its secret; the new limit applies to its next request.
   */
  async updateKnowledgeLimit(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = String(currentOrgContext().orgId);
      const userId = String(req.user?.userId ?? '');
      const tokenId = req.params.tokenId ?? '';
      // Validated by updatePatKnowledgeLimitSchema before this runs.
      const { knowledgeLimit } = req.body as UpdatePatKnowledgeLimitBody;
      const token = await this.patService.updateKnowledgeLimit(
        orgId,
        userId,
        tokenId,
        knowledgeLimit,
      );
      // A record of who changed a token's reach. Items are counted, never named.
      emitPlatformEvent('personal_access_token.knowledge_limit.changed', {
        orgId,
        userId,
        emailDomain: emailDomain(req.user?.email as string | undefined),
        limited: knowledgeLimit !== null,
        itemCount: knowledgeLimitSize(knowledgeLimit),
        ...requestClient(req.headers),
      });
      res.json({ token });
    } catch (error) {
      next(error);
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

  /**
   * List every active personal access token in the org, across all
   * users. Route-gated to org admins (see `userAdminCheck` in
   * `pat.routes.ts`) — for incident response on a credential its owner
   * didn't revoke themselves (departed employee, compromised laptop).
   */
  async adminListTokens(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const page = req.query.page ? parseInt(req.query.page as string, 10) : undefined
      const limit = req.query.limit
        ? parseInt(req.query.limit as string, 10)
        : undefined

      const result = await this.patService.listAllTokens(orgId, page, limit)

      res.json(result)
    } catch (error) {
      next(error)
    }
  }

  /**
   * Revoke any user's personal access token in the org by id.
   * Route-gated to org admins (see `userAdminCheck` in `pat.routes.ts`).
   */
  async adminRevokeToken(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user!.orgId
      const adminUserId = req.user!.userId
      const tokenId = req.params.tokenId!
      const reason =
        typeof req.body?.reason === 'string' ? req.body.reason : undefined

      await this.patService.adminRevokeToken(orgId, adminUserId, tokenId, reason)

      res.json({ message: 'Personal access token revoked successfully' })
    } catch (error) {
      next(error)
    }
  }
}
