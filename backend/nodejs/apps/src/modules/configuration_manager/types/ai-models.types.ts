/**
 * Model configuration for AI models
 */
export interface AIModelConfiguration {
  provider: string;
  configuration: Record<string, any>;
  modelKey: string;
  isMultimodal: boolean;
  isDefault: boolean;
  isReasoning: boolean;
  contextLength?: number | null;
  modelFriendlyName?: string;
  [key: string]: any;
}

/**
 * Assignment of a specific model to a named role.
 * modelType refers to a top-level key in AIModelsConfig (e.g. "llm", "slm").
 * modelKey is the UUID of the model entry within that array.
 */
export interface ModelRoleAssignment {
  modelType: string;
  modelKey: string;
}

/**
 * AI Models Configuration structure
 */
export interface AIModelsConfig {
  ocr?: AIModelConfiguration[];
  embedding?: AIModelConfiguration[];
  slm?: AIModelConfiguration[];
  llm?: AIModelConfiguration[];
  reasoning?: AIModelConfiguration[];
  multiModal?: AIModelConfiguration[];
  imageGeneration?: AIModelConfiguration[];
  tts?: AIModelConfiguration[];
  stt?: AIModelConfiguration[];
  /**
   * @deprecated Prompts are now stored at /services/systemPrompts via SystemPromptsConfig.
   * These fields remain here only so the OSS backward-compat GET fallback can read
   * pre-upgrade data from the aiModels blob without a TypeScript error.
   */
  customSystemPrompt?: string;
  /** @deprecated See customSystemPrompt. */
  customSystemPromptWebSearch?: string;
  /** @deprecated See customSystemPrompt. */
  customSystemPromptAgent?: string;
  /**
   * Role-to-model assignments. Each key is a named role (e.g. "indexing") and
   * the value identifies which configured model handles that role.
   * When absent or when a specific role is unset, callers fall back to the
   * default model for that model type.
   */
  modelRoles?: Record<string, ModelRoleAssignment>;
}

/**
 * Dedicated blob stored at /services/systemPrompts.
 * Replaces the customSystemPrompt* fields that were previously embedded inside AIModelsConfig.
 */
export interface SystemPromptsConfig {
  customSystemPrompt?: string;
  customSystemPromptWebSearch?: string;
  customSystemPromptAgent?: string;
}

