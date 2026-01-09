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
  [key: string]: any;
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
  customSystemPrompt?: string;
}

