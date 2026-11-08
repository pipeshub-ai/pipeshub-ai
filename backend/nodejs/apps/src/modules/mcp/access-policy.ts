/**
 * What an org admin allows on the inbound `/mcp` endpoint: whether it answers at all, whether
 * tools that write are offered, and which tools are turned off. One entry per org in the
 * key-value store; nothing stored means the defaults, which change nothing.
 */

import { NextFunction, Request, Response } from 'express';
import { z } from 'zod';
import { getCurrentOrgId } from '../../libs/context/orgContext';
import { Logger } from '../../libs/services/logger.service';
import { KeyValueStoreService } from '../../libs/services/keyValueStore.service';
import { ServiceUnavailableError } from '../../libs/errors/http.errors';
import { loadConfigurationManagerConfig } from '../configuration_manager/config/config';
import { configPaths } from '../configuration_manager/paths/paths';
import { sendMcpError } from './middlewares/mcp-guard.middleware';
import { calledTools } from './step-up';

const logger = Logger.getInstance({ service: 'McpAccessPolicy' });

export const mcpAccessPolicySchema = z
  .object({
    enabled: z.boolean(),
    allowWriteTools: z.boolean(),
    disabledTools: z
      .array(z.string().regex(/^[a-z0-9_]{1,64}$/))
      .max(200)
      .transform((names) => [...new Set(names)].sort()),
  })
  .strict();

export type McpAccessPolicy = z.infer<typeof mcpAccessPolicySchema>;

export const DEFAULT_MCP_ACCESS_POLICY: McpAccessPolicy = {
  enabled: true,
  allowWriteTools: true,
  disabledTools: [],
};

export const MCP_ACCESS_OFF_MESSAGE =
  'MCP access is turned off for this organization by an admin.';

export const toolTurnedOffMessage = (tool: string): string =>
  `The ${tool} tool is turned off for this organization by an admin.`;

/** How long a process keeps an org's policy before reading it again: an admin's change reaches every instance within this. */
export const MCP_ACCESS_POLICY_TTL_MS = 5_000;

const CACHE_MAX_KEYS = 10_000;

const cached = new Map<string, { policy: McpAccessPolicy; at: number }>();

function remember(orgId: string, policy: McpAccessPolicy): void {
  cached.delete(orgId);
  if (cached.size >= CACHE_MAX_KEYS) {
    const oldest = cached.keys().next().value;
    if (oldest !== undefined) cached.delete(oldest);
  }
  cached.set(orgId, { policy, at: Date.now() });
}

const keyFor = (orgId: string): string =>
  `${configPaths.mcpAccessPolicy}/${orgId}`;

export const McpAccessPolicyStore = {
  store(): Pick<KeyValueStoreService, 'get' | 'set' | 'isConnected'> {
    return KeyValueStoreService.getInstance(loadConfigurationManagerConfig());
  },

  /**
   * The org's policy. Until the store has connected (boot) the defaults apply, as for every
   * other settings read. A failed read keeps the last policy this process saw for the org;
   * with none, it throws, so an org an admin turned off is not opened by a store outage.
   */
  async read(orgId: string): Promise<McpAccessPolicy> {
    const hit = cached.get(orgId);
    if (hit && Date.now() - hit.at < MCP_ACCESS_POLICY_TTL_MS) {
      return hit.policy;
    }
    const store = McpAccessPolicyStore.store();
    if (!store.isConnected()) return DEFAULT_MCP_ACCESS_POLICY;
    try {
      const raw = await store.get<string>(keyFor(orgId));
      const policy =
        raw !== null && raw !== ''
          ? mcpAccessPolicySchema.parse(JSON.parse(raw))
          : DEFAULT_MCP_ACCESS_POLICY;
      remember(orgId, policy);
      return policy;
    } catch (error) {
      logger.warn('Could not read the MCP access policy', {
        orgId,
        error: error instanceof Error ? error.message : String(error),
      });
      if (hit) return hit.policy;
      throw new ServiceUnavailableError(
        'MCP access settings could not be read. Try again shortly.',
      );
    }
  },

  async write(orgId: string, policy: McpAccessPolicy): Promise<void> {
    const store = McpAccessPolicyStore.store();
    if (!store.isConnected()) {
      throw new ServiceUnavailableError(
        'MCP access settings cannot be saved yet. Try again shortly.',
      );
    }
    await store.set<string>(keyFor(orgId), JSON.stringify(policy));
    remember(orgId, policy);
  },

  /** Drops the cached policies, so a test sees the policy it just stored. */
  resetCache(): void {
    cached.clear();
  },
};

/** The policy `mcpAccessGuard` loaded for this request; the defaults when it did not run. */
export function mcpAccessPolicyOf(res: Response): McpAccessPolicy {
  const locals = res.locals as Record<string, unknown> | undefined;
  return (
    (locals?.mcpAccessPolicy as McpAccessPolicy | undefined) ??
    DEFAULT_MCP_ACCESS_POLICY
  );
}

/** Answers every request of an org whose admin turned MCP off, and hands the policy on. */
export async function mcpAccessGuard(
  req: Request,
  res: Response,
  next: NextFunction,
): Promise<void> {
  const fromUser = (req as Request & { user?: { orgId?: string } }).user?.orgId;
  const orgId = getCurrentOrgId() ?? fromUser;
  // Without an org there is no policy to apply, and skipping it would reopen an org an admin turned off.
  if (orgId === undefined || orgId === '') {
    sendMcpError(
      res,
      503,
      'MCP access settings could not be read. Try again shortly.',
    );
    return;
  }
  let policy: McpAccessPolicy;
  try {
    policy = await McpAccessPolicyStore.read(orgId);
  } catch (error) {
    sendMcpError(
      res,
      503,
      error instanceof Error ? error.message : 'Try again shortly.',
    );
    return;
  }
  if (!policy.enabled) {
    sendMcpError(res, 403, MCP_ACCESS_OFF_MESSAGE);
    return;
  }
  res.locals.mcpAccessPolicy = policy;
  next();
}

/**
 * The first tool a tools/call in `body` names that the policy turns off, or undefined.
 * `isWriteTool` is asked only while write tools are off.
 */
export async function toolTurnedOff(
  body: unknown,
  policy: McpAccessPolicy,
  isWriteTool: () => Promise<(name: string) => boolean>,
): Promise<string | undefined> {
  const called = calledTools(body);
  if (called.length === 0) return undefined;
  const disabled = called.find((name) => policy.disabledTools.includes(name));
  if (disabled !== undefined || policy.allowWriteTools) return disabled;
  const writes = await isWriteTool();
  return called.find(writes);
}
