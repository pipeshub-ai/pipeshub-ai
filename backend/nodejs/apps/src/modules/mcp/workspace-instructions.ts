/**
 * The workspace's own instructions for MCP clients: the Agent prompt from
 * Workspace → Prompts, when the admin switched on sending it to MCP. The
 * server puts them ahead of its own instructions.
 */

import { Logger } from '../../libs/services/logger.service';
import {
  readSystemPrompts,
  SystemPromptsReadStore,
} from '../configuration_manager/utils/system-prompts';

const logger = Logger.getInstance({ service: 'McpWorkspaceInstructions' });

/** Where an org's prompts are read from. */
export type SystemPromptsStoreForOrg = (
  orgId: string,
) => Promise<SystemPromptsReadStore>;

/** How long a process keeps an org's instructions: an admin's save reaches every instance within this. */
export const MCP_WORKSPACE_INSTRUCTIONS_TTL_MS = 5_000;

const CACHE_MAX_KEYS = 10_000;

const cached = new Map<string, { text: string | undefined; at: number }>();

function remember(orgId: string, text: string | undefined): void {
  cached.delete(orgId);
  if (cached.size >= CACHE_MAX_KEYS) {
    const oldest = cached.keys().next().value;
    if (oldest !== undefined) cached.delete(oldest);
  }
  cached.set(orgId, { text, at: Date.now() });
}

export const McpWorkspaceInstructions = {
  /**
   * The text to send, or undefined when the switch is off or the prompt is
   * blank. A failed read sends what this process last read, or nothing: the
   * server still answers with its own instructions.
   */
  async read(
    orgId: string,
    storeForOrg: SystemPromptsStoreForOrg,
  ): Promise<string | undefined> {
    const hit = cached.get(orgId);
    if (hit && Date.now() - hit.at < MCP_WORKSPACE_INSTRUCTIONS_TTL_MS) {
      return hit.text;
    }
    try {
      const store = await storeForOrg(orgId);
      // While the store is not connected (at boot, or after losing it), send what
      // this process last read, or nothing: a read now would wait for it.
      if (store.isConnected?.() === false) return hit?.text;
      const prompts = await readSystemPrompts(store);
      const prompt = prompts.customSystemPromptAgent.trim();
      const text = prompts.useInMcp && prompt !== '' ? prompt : undefined;
      remember(orgId, text);
      return text;
    } catch (error) {
      logger.warn('Could not read the workspace instructions for MCP', {
        orgId,
        error: error instanceof Error ? error.message : String(error),
      });
      return hit?.text;
    }
  },

  /** Drops the cached instructions, so a test reads the prompts it just stored. */
  resetCache(): void {
    cached.clear();
  },
};

/** Whether a JSON-RPC message or batch is answered with the server's instructions. */
export function sendsInstructions(body: unknown): boolean {
  if (Array.isArray(body)) return body.some(sendsInstructions);
  const method = (body as { method?: unknown } | undefined)?.method;
  return method === 'initialize' || method === 'prompts/get';
}
