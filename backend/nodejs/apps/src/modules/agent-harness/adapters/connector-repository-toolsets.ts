import { z } from 'zod/v3';
import { ConnectorServiceCommand } from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { GITHUB_TOOLSET } from '../domain/repository';
import type { RepositoryToolsets } from '../ports/repository-toolsets';

const MY_TOOLSETS_PATH = '/api/v1/toolsets/my-toolsets';
// The store's own page cap: an org with more GitHub instances than this is not a case v1 serves.
const PAGE_LIMIT = 200;
const TIMEOUT_MS = 10_000;

const listedSchema = z
  .object({
    toolsets: z.array(
      z
        .object({
          instanceId: z.string(),
          toolsetType: z.string(),
          isAuthenticated: z.boolean().optional(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

/** The toolset store could not be asked or answered something else than a list. */
export class RepositoryToolsetsUnavailableError extends Error {
  constructor(public readonly statusCode: number) {
    super(`The toolset store answered ${String(statusCode)}`);
    this.name = 'RepositoryToolsetsUnavailableError';
  }
}

/**
 * The connectors service's "My toolsets" (admin-made and the caller's own instances, each with the caller's sign-in
 * state), asked with the caller's own Authorization header, so it answers only what that person may use.
 */
export class ConnectorRepositoryToolsets implements RepositoryToolsets {
  constructor(private readonly connectorBackend: () => string) {}

  async usableGithubInstance(request: {
    orgId: string;
    userId: string;
    instanceId: string;
    authorization: string;
  }): Promise<boolean> {
    const query = new URLSearchParams({
      toolsetType: GITHUB_TOOLSET,
      limit: String(PAGE_LIMIT),
    });
    const response = await new ConnectorServiceCommand<unknown>({
      uri: `${this.connectorBackend()}${MY_TOOLSETS_PATH}?${query.toString()}`,
      method: HttpMethod.GET,
      headers: { authorization: request.authorization },
      timeoutMs: TIMEOUT_MS,
      retries: 1,
    }).execute();
    if (response.statusCode !== 200) {
      throw new RepositoryToolsetsUnavailableError(response.statusCode);
    }
    const parsed = listedSchema.safeParse(response.data);
    if (!parsed.success) throw new RepositoryToolsetsUnavailableError(200);
    return parsed.data.toolsets.some(
      (toolset) =>
        toolset.instanceId === request.instanceId &&
        toolset.toolsetType.trim().toLowerCase() === GITHUB_TOOLSET &&
        toolset.isAuthenticated === true,
    );
  }
}
