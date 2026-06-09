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
  customSystemPrompt?: string;
  customSystemPromptWebSearch?: string;
  /**
   * Role-to-model assignments. Each key is a named role (e.g. "indexing") and
   * the value identifies which configured model handles that role.
   * When absent or when a specific role is unset, callers fall back to the
   * default model for that model type.
   */
  modelRoles?: Record<string, ModelRoleAssignment>;
}

