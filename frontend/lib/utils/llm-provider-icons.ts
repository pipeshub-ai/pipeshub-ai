/** Static LLM provider artwork under `public/icons/ai-models/`. */
export const AGENT_LLM_ICONS_BASE = '/icons/ai-models';

/** When provider is unknown or the asset fails to load. */
export const AGENT_LLM_FALLBACK_ICON = `${AGENT_LLM_ICONS_BASE}/default.svg`;

/**
 * Maps API `provider` ids (see onboarding / model catalog) to filenames under
 * `public/icons/ai-models/`.
 */
const LLM_PROVIDER_ICON_FILE: Record<string, string> = {
  openAI: 'openai.svg',
  gemini: 'gemini-color.svg',
  azureAI: 'azure-color.svg',
  azureOpenAI: 'azure-color.svg',
  cohere: 'cohere-color.svg',
  bedrock: 'bedrock-color.svg',
  ollama: 'ollama.svg',
  openAICompatible: 'openai.svg',
  together: 'together-color.svg',
  anthropic: 'claude-color.svg',
  groq: 'groq.svg',
  minimax: 'minimax.svg',
  xai: 'xai.svg',
  fireworks: 'fireworks-color.svg',
  mistral: 'mistral-color.svg',
  sentenceTransformers: 'sentence-transformers.png',
  jinaAI: 'jina.svg',
  voyage: 'voyage-color.svg',
  huggingFace: 'huggingface-color.svg',
  vertexAI: 'Vertex-AI.svg',
};

const LLM_PROVIDER_ICON_LOOKUP: Map<string, string> = (() => {
  const m = new Map<string, string>();
  for (const [id, file] of Object.entries(LLM_PROVIDER_ICON_FILE)) {
    m.set(id, file);
    m.set(id.toLowerCase(), file);
  }
  return m;
})();

/**
 * Resolves the public URL for an LLM provider icon. Always returns a string
 * (`default.svg` when the provider is missing or not mapped).
 */
export function resolveLlmProviderIconPath(provider: string | undefined | null): string {
  const key = typeof provider === 'string' ? provider.trim() : '';
  if (!key) return AGENT_LLM_FALLBACK_ICON;
  const file =
    LLM_PROVIDER_ICON_LOOKUP.get(key) ?? LLM_PROVIDER_ICON_LOOKUP.get(key.toLowerCase()) ?? 'default.svg';
  return `${AGENT_LLM_ICONS_BASE}/${file}`;
}
