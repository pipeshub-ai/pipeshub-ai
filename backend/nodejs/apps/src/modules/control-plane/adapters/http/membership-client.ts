import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../../libs/services/authtoken.service';
import type { TeamMembership } from '../../domain/resolution';
import type { MembershipPort } from '../../ports/directory';
import type { LegacyCapabilitySourceConfig } from './legacy-capability-source';

const usersPath = (userId: string): string =>
  `/api/v1/control-plane/drivers/directory/users/${encodeURIComponent(userId)}/teams`;
const ownersPath = (teamId: string): string =>
  `/api/v1/control-plane/drivers/directory/teams/${encodeURIComponent(teamId)}/owners`;
const TIMEOUT_MS = 10_000;
const TOKEN_TTL = '5m';

const answer = z.object({
  teams: z.array(
    z.object({ teamId: z.string().min(1), role: z.string().min(1) }),
  ),
});

const owners = z.object({ owners: z.number().int().min(0) });

const ownerIds = z.object({ ownerIds: z.array(z.string().min(1)) });

/** Team membership was not read; the Authorizer's team rule and a pin fail closed on it, never on `[]`. */
export class MembershipUnavailableError extends Error {
  constructor(readonly statusCode: number) {
    super(`The team directory answered ${String(statusCode)}`);
    this.name = 'MembershipUnavailableError';
  }
}

/**
 * The query service's team directory (CPD-11, 03-interfaces §5.5): a service
 * token scoped `directory:read` whose org is the one asked about, so only
 * that org's teams come back. Front it with `CachingMembershipPort`.
 */
export class HttpMembershipPort implements MembershipPort {
  constructor(private readonly config: () => LegacyCapabilitySourceConfig) {}

  async teamsOf(
    orgId: string,
    userId: string,
  ): Promise<readonly TeamMembership[]> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.DIRECTORY_READ] },
      TOKEN_TTL,
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.aiBackend}${usersPath(userId)}`,
      method: HttpMethod.GET,
      headers: { Authorization: `Bearer ${token}` },
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 2,
    }).execute();
    if (response.statusCode !== 200)
      throw new MembershipUnavailableError(response.statusCode);
    const parsed = answer.safeParse(response.data);
    if (!parsed.success) throw new MembershipUnavailableError(200);
    return parsed.data.teams;
  }

  async teamOwnerCount(orgId: string, teamId: string): Promise<number> {
    const parsed = owners.safeParse(await this.owners(orgId, teamId, false));
    if (!parsed.success) throw new MembershipUnavailableError(200);
    return parsed.data.owners;
  }

  async teamOwnerIds(
    orgId: string,
    teamId: string,
  ): Promise<readonly string[]> {
    const parsed = ownerIds.safeParse(await this.owners(orgId, teamId, true));
    if (!parsed.success) throw new MembershipUnavailableError(200);
    return parsed.data.ownerIds;
  }

  private async owners(
    orgId: string,
    teamId: string,
    ids: boolean,
  ): Promise<unknown> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.DIRECTORY_READ] },
      TOKEN_TTL,
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.aiBackend}${ownersPath(teamId)}${ids ? '?ids=true' : ''}`,
      method: HttpMethod.GET,
      headers: { Authorization: `Bearer ${token}` },
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 2,
    }).execute();
    if (response.statusCode !== 200)
      throw new MembershipUnavailableError(response.statusCode);
    return response.data;
  }
}
