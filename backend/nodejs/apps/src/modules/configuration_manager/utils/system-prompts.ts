import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../config/config';
import { configPaths } from '../paths/paths';
import { SystemPromptsConfig } from '../types/ai-models.types';

/** The stored prompts, every field filled: '' for a prompt never set. */
export type StoredSystemPrompts = Required<SystemPromptsConfig>;

/** The one read the prompts need from a key-value store. */
export interface SystemPromptsReadStore {
  get<T>(key: string): Promise<T | null>;
  /** False until the store has connected; a read then would wait for it. */
  isConnected?(): boolean;
}

/**
 * The longest Agent prompt that may be sent to MCP clients. It is the
 * `MAX_CUSTOM_INSTRUCTIONS_CHARS` of `@pipeshub-ai/mcp` (a contract test keeps
 * the two equal): with it, the server's instructions still fit what a client
 * keeps of them.
 */
export const MCP_PROMPT_MAX_CHARS = 500;

/** The length MCP_PROMPT_MAX_CHARS limits: leading and trailing space is not sent. */
export const mcpPromptLength = (prompt: string): number => prompt.trim().length;

const decrypt = (encrypted: string): string => {
  const { algorithm, secretKey } = loadConfigurationManagerConfig();
  return EncryptionService.getInstance(algorithm, secretKey).decrypt(encrypted);
};

/**
 * The workspace's prompts from `store`. Prompts saved before they had their
 * own key are read from the AI models configuration, and are never sent to
 * MCP clients.
 */
export async function readSystemPrompts(
  store: SystemPromptsReadStore,
): Promise<StoredSystemPrompts> {
  const encrypted = await store.get<string>(configPaths.systemPrompts);
  if (encrypted !== null && encrypted !== '') {
    const stored = JSON.parse(decrypt(encrypted)) as SystemPromptsConfig;
    return {
      customSystemPrompt: stored.customSystemPrompt ?? '',
      customSystemPromptWebSearch: stored.customSystemPromptWebSearch ?? '',
      customSystemPromptAgent: stored.customSystemPromptAgent ?? '',
      useInMcp: stored.useInMcp === true,
    };
  }
  // The AI models configuration held the three prompts, under the same names.
  const legacy = await store.get<string>(configPaths.aiModels);
  const old =
    legacy !== null && legacy !== ''
      ? (JSON.parse(decrypt(legacy)) as SystemPromptsConfig)
      : {};
  return {
    customSystemPrompt: old.customSystemPrompt ?? '',
    customSystemPromptWebSearch: old.customSystemPromptWebSearch ?? '',
    customSystemPromptAgent: old.customSystemPromptAgent ?? '',
    useInMcp: false,
  };
}

/**
 * The store that holds `orgId`'s prompts. This build keeps one workspace's
 * configuration, so it is the store itself.
 */
export function systemPromptsStoreForOrg(
  _orgId: string,
): Promise<SystemPromptsReadStore> {
  return Promise.resolve(
    KeyValueStoreService.getInstance(loadConfigurationManagerConfig()),
  );
}
