/**
 * Admin settings for the inbound `/mcp` endpoint, per org. The configuration manager router
 * serves them at `/api/v1/configurationManager/mcp/access`, behind its admin check.
 */

import { NextFunction, Request, Response } from 'express';
import { z } from 'zod';
import { currentOrgContext } from '../../../libs/context/orgContext';
import {
  McpAccessPolicy,
  McpAccessPolicyStore,
  mcpAccessPolicySchema,
} from '../access-policy';
import { McpToolCatalog } from '../tool-catalog';

export const putMcpAccessPolicySchema = z.object({
  body: mcpAccessPolicySchema,
});

/** userAdminCheck has already refused a caller without an org. */
const callerOrgId = (): string => String(currentOrgContext().orgId);

export async function getMcpAccessPolicy(
  _req: Request,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    res.status(200).json(await McpAccessPolicyStore.read(callerOrgId()));
  } catch (error) {
    next(error);
  }
}

export async function putMcpAccessPolicy(
  req: Request,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    const policy = req.body as McpAccessPolicy;
    await McpAccessPolicyStore.write(callerOrgId(), policy);
    res.status(200).json(policy);
  } catch (error) {
    next(error);
  }
}

export async function listMcpAccessTools(
  _req: Request,
  res: Response,
  next: NextFunction,
): Promise<void> {
  try {
    res.status(200).json({ tools: await McpToolCatalog.list() });
  } catch (error) {
    next(error);
  }
}
