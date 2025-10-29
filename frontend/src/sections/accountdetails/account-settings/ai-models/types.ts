export type ModelType = 'llm' | 'embedding';
export type ProviderId = string;

export interface ConfiguredModel {
  id: string;
  modelKey?: string;
  name: string;
  provider: string;
  modelType: ModelType;
  configuration: Record<string, any>;
  isActive: boolean;
  isDefault: boolean;
  isMultimodal?: boolean;
  isReasoning?: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface ModelProvider {
  id: string;
  name: string;
  description: string;
  supportedTypes: ModelType[];
  isPopular?: boolean;
  src: string;
  color: string;
}

export interface ModelData {
  provider: string;
  configuration: Record<string, any>;
  isMultimodal?: boolean;
  isReasoning?: boolean;
  isDefault?: boolean;
  name?: string;
}

export const AVAILABLE_MODEL_PROVIDERS: ModelProvider[] = [
  {
    id: 'openAI',
    name: 'OpenAI',
    description: 'GPT models for text generation and embeddings',
    src: '/assets/icons/ai-models/openai.svg',
    supportedTypes: ['llm', 'embedding'],
    isPopular: true,
    color: '#10A37F',
  },
  {
    id: 'gemini',
    name: 'Gemini',
    description: 'Gemini models with multimodal capabilities',
    src: '/assets/icons/ai-models/gemini-color.svg',
    supportedTypes: ['llm', 'embedding'],
    isPopular: true,
    color: '#4285F4',
  },
  {
    id: 'azureOpenAI',
    name: 'Azure-OpenAI',
    description: 'Enterprise-grade OpenAI models',
    src: '/assets/icons/ai-models/azure-color.svg',
    supportedTypes: ['llm', 'embedding'],
    color: '#0078D4',
  },
  {
    id: 'cohere',
    name: 'Cohere',
    description: 'Command models for text generation and embeddings',
    src: '/assets/icons/ai-models/cohere-color.svg',
    supportedTypes: ['llm', 'embedding'],
    color: '#39C5BB',
  },
  {
    id: 'bedrock',
    name: 'Bedrock',
    description: 'AWS Bedrock models',
    src: '/assets/icons/ai-models/bedrock-color.svg',
    supportedTypes: ['llm', 'embedding'],
    color: '#0078D4',
  },
  {
    id: 'ollama',
    name: 'Ollama',
    description: 'Local open-source models',
    src: '/assets/icons/ai-models/ollama.svg',
    supportedTypes: ['llm', 'embedding'],
    color: '#4A90E2',
  },
  {
    id: 'openAICompatible',
    name: 'OpenAI Compatible',
    description: 'OpenAI-compatible models',
    src: '/assets/icons/ai-models/openai.svg',
    supportedTypes: ['llm', 'embedding'],
    color: '#0078D4',
  },
  {
    id: 'together',
    name: 'Together',
    description: 'Open-source models at scale',
    src: '/assets/icons/ai-models/together-color.svg',
    supportedTypes: ['llm', 'embedding'],
    color: '#7C3AED',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    description: 'Claude models for advanced text processing',
    src: '/assets/icons/ai-models/claude-color.svg',
    supportedTypes: ['llm'],
    isPopular: true,
    color: '#D97706',
  },
  {
    id: 'groq',
    name: 'Groq',
    description: 'High-speed inference for LLM models',
    src: '/assets/icons/ai-models/groq.svg',
    supportedTypes: ['llm'],
    color: '#F55036',
  },
  {
    id: 'xai',
    name: 'XAI',
    description: 'Grok models with real-time capabilities',
    src: '/assets/icons/ai-models/xai.svg',
    supportedTypes: ['llm'],
    color: '#1DA1F2',
  },
  {
    id: 'fireworks',
    name: 'Fireworks',
    description: 'Fast inference for generative AI',
    src: '/assets/icons/ai-models/fireworks-color.svg',
    supportedTypes: ['llm'],
    color: '#FF6B35',
  },
  {
    id: 'mistral',
    name: 'Mistral',
    description: 'High-performance language models',
    src: '/assets/icons/ai-models/mistral-color.svg',
    supportedTypes: ['llm'],
    color: '#FF7000',
  },

  {
    id: 'sentenceTransformers',
    name: 'Sentence Transformers',
    description: 'Sentence Transformers models',
    src: '/assets/icons/ai-models/sentence-transformers.png',
    supportedTypes: ['embedding'],
    color: '#0078D4',
  },
  {
    id: 'jinaAI',
    name: 'Jina AI',
    description: 'Jina AI models',
    src: '/assets/icons/ai-models/jina.svg',
    supportedTypes: ['embedding'],
    color: '#0078D4',
  },
  {
    id: 'voyage',
    name: 'Voyage',
    description: 'Voyage models',
    src: '/assets/icons/ai-models/voyage-color.svg',
    supportedTypes: ['embedding'],
    color: '#0078D4',
  },
  {
    id: 'huggingface',
    name: 'HuggingFace (System Provided)',
    description: 'Open-source transformer models',
    supportedTypes: ['embedding'],
    src: '/assets/icons/ai-models/huggingface-color.svg',
    color: '#FFD21E',
  },
];

export const MODEL_TYPE_CONFIGS = {
  llm: {
    name: 'Large Language Models',
    description: 'Text generation and comprehension models',
    icon: 'carbon:machine-learning-model',
    color: '#4CAF50',
  },
  embedding: {
    name: 'Embedding Models',
    description: 'Text vectorization for semantic search',
    icon: 'mdi:magnify',
    color: '#9C27B0',
  },
};

// Rest of the interfaces remain the same...
export interface ApiResponse {
  status: string;
  data?: any;
  models?: ConfiguredModel[];
  message?: string;
}

export interface ModelConfig {
  modelType: string;
  [key: string]: any;
}

export interface HealthCheckResult {
  success: boolean;
  message?: string;
}

export interface ModelTemplate {
  id: string;
  name: string;
  provider: string;
  configuration: Record<string, any>;
}

export interface ModelStatistics {
  totalModels: number;
  llmModels: number;
  embeddingModels: number;
  defaultModels: number;
}
