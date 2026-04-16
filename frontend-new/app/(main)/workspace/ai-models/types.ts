// ========================================
// Registry types (from Python backend)
// ========================================

export interface AIModelProviderField {
  name: string;
  displayName: string;
  fieldType: 'TEXT' | 'PASSWORD' | 'SELECT' | 'NUMBER' | 'BOOLEAN' | 'URL' | 'TEXTAREA' | 'CHECKBOX';
  required: boolean;
  defaultValue?: unknown;
  placeholder?: string;
  description?: string;
  isSecret?: boolean;
  options?: { value: string; label: string }[];
  validation?: { minLength?: number; maxLength?: number; pattern?: string };
}

export interface AIModelProvider {
  providerId: string;
  name: string;
  description: string;
  capabilities: string[];
  iconPath: string;
  color: string;
  isPopular?: boolean;
  fields: Record<string, AIModelProviderField[]>;
}

export interface RegistryResponse {
  success: boolean;
  providers: AIModelProvider[];
  total: number;
}

export interface CapabilityInfo {
  id: string;
  name: string;
  modelType: string;
}

export interface CapabilitiesResponse {
  success: boolean;
  capabilities: CapabilityInfo[];
}

export interface ProviderSchemaResponse {
  success: boolean;
  provider: { providerId: string; name: string };
  schema: { fields: Record<string, AIModelProviderField[]> };
}

// ========================================
// Configured model types (from CRUD API)
// ========================================

export interface ConfiguredModel {
  modelKey: string;
  provider: string;
  modelType: string;
  configuration: Record<string, unknown>;
  isMultimodal?: boolean;
  isReasoning?: boolean;
  isDefault: boolean;
  contextLength?: number | null;
  modelFriendlyName?: string;
}

export interface AllModelsResponse {
  status: string;
  models: {
    ocr: ConfiguredModel[];
    embedding: ConfiguredModel[];
    slm: ConfiguredModel[];
    llm: ConfiguredModel[];
    reasoning: ConfiguredModel[];
    multiModal: ConfiguredModel[];
  };
  message: string;
}

export interface ModelsByTypeResponse {
  status: string;
  models: ConfiguredModel[];
  message: string;
}

// ========================================
// Capability / model type mapping
// ========================================

export const CAPABILITY_TO_MODEL_TYPE: Record<string, string> = {
  text_generation: 'llm',
  embedding: 'embedding',
  ocr: 'ocr',
  reasoning: 'reasoning',
  image_generation: 'imageGeneration',
  tts: 'tts',
  stt: 'stt',
  video: 'video',
};

export const MODEL_TYPE_TO_CAPABILITY: Record<string, string> = Object.fromEntries(
  Object.entries(CAPABILITY_TO_MODEL_TYPE).map(([k, v]) => [v, k])
);

export const CAPABILITY_DISPLAY_NAMES: Record<string, string> = {
  text_generation: 'LLM',
  embedding: 'Embedding',
  ocr: 'OCR',
  reasoning: 'Reasoning',
  image_generation: 'Image Generation',
  tts: 'Text to Speech',
  stt: 'Speech to Text',
  video: 'Video',
};
