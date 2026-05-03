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
  /**
   * Labeled example values shown below the input as a compact, copyable note.
   * Useful when a single placeholder can't convey all the variants a user
   * might need (e.g. Azure AI endpoints differ per model family).
   */
  examples?: { label: string; value: string }[];
}

export interface AIModelProvider {
  providerId: string;
  name: string;
  description: string;
  /** Optional operational warning (e.g. performance); shown in configure UI when set. */
  notice?: string;
  modelName?: string;
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
    imageGeneration?: ConfiguredModel[];
    tts?: ConfiguredModel[];
    stt?: ConfiguredModel[];
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

/** Primary capability tabs on the AI Models page (registry + configured filtering). */
export type CapabilitySection =
  | 'text_generation'
  | 'embedding'
  | 'image_generation'
  | 'tts'
  | 'stt';

/** Order of primary capability section tabs; labels come from i18n. */
export const CAPABILITY_SECTION_ORDER: CapabilitySection[] = [
  'text_generation',
  'embedding',
  'image_generation',
  'tts',
  'stt',
];

/** Model API buckets shown under the "For LLMs" tab. */
export const LLM_SECTION_MODEL_TYPES = ['llm', 'reasoning', 'multiModal', 'slm'] as const;

/** Map configured `modelType` to registry capability key for schema / edit dialog. */
export function registryCapabilityForModelType(modelType: string): string {
  const fromMap = MODEL_TYPE_TO_CAPABILITY[modelType];
  if (fromMap) return fromMap;
  if (modelType === 'multiModal' || modelType === 'slm') return 'text_generation';
  return 'text_generation';
}

/** Registry capability keys that show a badge on provider rows; badge text from i18n. */
export const REGISTRY_BADGE_CAPABILITY_KEYS = [
  'text_generation',
  'reasoning',
  'video',
  'image_generation',
  'embedding',
  'tts',
  'stt',
] as const;

export type RegistryBadgeCapabilityKey = (typeof REGISTRY_BADGE_CAPABILITY_KEYS)[number];

export function isRegistryBadgeCapability(cap: string): cap is RegistryBadgeCapabilityKey {
  return (REGISTRY_BADGE_CAPABILITY_KEYS as readonly string[]).includes(cap);
}
