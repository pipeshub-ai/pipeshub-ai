import { z } from 'zod/v3';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import { PersonalInstanceEraser } from '../ports/personal-instances';

const ERASE_PATH = '/api/v1/entity/internal/personal-instances/erase';
const TIMEOUT_MS = 60_000;
const TOKEN_TTL = '5m';

const erasedSchema = z
  .object({ toolsetInstances: z.number().int().nonnegative() })
  .passthrough();

export interface PersonalInstanceEraserConfig {
  connectorBackend: string;
  jwtSecret: string;
  scopedJwtSecret: string;
}

/** The connectors service answered something other than "erased"; the deletion stops and is retried. */
export class PersonalInstanceErasureFailedError extends Error {
  constructor(public readonly statusCode: number) {
    super(`Personal instance erasure answered ${String(statusCode)}`);
    this.name = 'PersonalInstanceErasureFailedError';
  }
}

/**
 * The connectors service's erase route for a deleted principal's personal
 * toolset and MCP instances: a service token scoped `principal:erase`, its
 * org the deleted one's; the body names only the user (none: the whole org).
 * The connectors service runs the token refreshers, so their tasks stop there
 * too. It answers 503 while any key remains, which this throws.
 */
export class ConnectorPersonalInstanceEraser implements PersonalInstanceEraser {
  constructor(private readonly config: () => PersonalInstanceEraserConfig) {}

  eraseUser(orgId: string, userId: string): Promise<number> {
    return this.erase(orgId, { userId });
  }

  eraseOrg(orgId: string): Promise<number> {
    return this.erase(orgId, {});
  }

  private async erase(
    orgId: string,
    body: { userId?: string },
  ): Promise<number> {
    const config = this.config();
    const token = new AuthTokenService(
      config.jwtSecret,
      config.scopedJwtSecret,
    ).generateScopedToken(
      { orgId, scopes: [TokenScopes.PRINCIPAL_ERASE] },
      TOKEN_TTL,
    );
    const response = await new AIServiceCommand<unknown>({
      uri: `${config.connectorBackend}${ERASE_PATH}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body,
      timeoutMs: TIMEOUT_MS,
    }).execute();
    if (response.statusCode !== 200) {
      throw new PersonalInstanceErasureFailedError(response.statusCode);
    }
    const parsed = erasedSchema.safeParse(response.data);
    if (!parsed.success) throw new PersonalInstanceErasureFailedError(200);
    return parsed.data.toolsetInstances;
  }
}
