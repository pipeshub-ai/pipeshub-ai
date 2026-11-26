import { AIServiceCommand } from '../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../../libs/services/authtoken.service';
import {
  legacyCapabilitiesSchema,
  type LegacyCapabilities,
} from '../../contracts/generated';
import type {
  LegacyCapabilitySource,
  LegacyPageOptions,
} from '../../ports/capability-ledger';

export const LEGACY_CAPABILITIES_PATH =
  '/api/v1/control-plane/drivers/legacy/capabilities';
const TIMEOUT_MS = 30_000;
const TOKEN_TTL = '5m';

export interface LegacyCapabilitySourceConfig {
  readonly aiBackend: string;
  readonly jwtSecret: string;
  readonly scopedJwtSecret: string;
}

/** The legacy driver did not answer one org's facts; `reason` is what the migration report names. */
export class LegacySourceUnavailableError extends Error {
  readonly reason = 'legacy_source_unavailable';

  constructor(readonly statusCode: number) {
    super(`The legacy capability driver answered ${String(statusCode)}`);
    this.name = 'LegacySourceUnavailableError';
  }
}

/**
 * The query service's legacy capability driver (CPD-7): a service token
 * scoped `capabilities:read` whose org is the one asked about, so the driver
 * answers only that org's facts (MCP instances through SEC5's org-scoped
 * lookup). Every answer is held to the generated contract before use.
 */
export class HttpLegacyCapabilitySource implements LegacyCapabilitySource {
  constructor(private readonly config: () => LegacyCapabilitySourceConfig) {}

  async page(
    orgId: string,
    options: LegacyPageOptions,
  ): Promise<LegacyCapabilities> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.CAPABILITIES_READ] },
      TOKEN_TTL,
    );
    const query = new URLSearchParams();
    if (options.after !== null) query.set('after', options.after);
    if (options.agentKey !== undefined) query.set('agentKey', options.agentKey);
    if (options.agents === false) query.set('agents', 'false');
    const suffix = query.size > 0 ? `?${query.toString()}` : '';
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.aiBackend}${LEGACY_CAPABILITIES_PATH}${suffix}`,
      method: HttpMethod.GET,
      headers: { Authorization: `Bearer ${token}` },
      timeoutMs: TIMEOUT_MS,
      maxAttempts: 2,
    }).execute();
    if (response.statusCode !== 200)
      throw new LegacySourceUnavailableError(response.statusCode);
    const parsed = legacyCapabilitiesSchema.safeParse(response.data);
    if (!parsed.success) throw new LegacySourceUnavailableError(200);
    return parsed.data;
  }
}
