import { ChatMode } from '../types';

// Define chat modes locally in the frontend
export const CHAT_MODES: ChatMode[] = [
  {
    id: 'internal_search',
    name: 'Internal Search',
    description: 'Answer only from internal knowledge base',
  },
  {
    id: 'web_search',
    name: 'Web Search',
    description: 'Search the web for answers',
  },
];

export const normalizeDisplayName = (name: string): string =>
  name
    .split('_')
    .map((word) => {
      const upperWord = word.toUpperCase();
      if (
        [
          'ID',
          'URL',
          'API',
          'UI',
          'DB',
          'AI',
          'ML',
          'KB',
          'PDF',
          'CSV',
          'JSON',
          'XML',
          'HTML',
          'CSS',
          'JS',
          'GCP',
          'AWS',
        ].includes(upperWord)
      ) {
        return upperWord;
      }
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(' ');

export const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  azureAI: 'Azure AI',
  azureOpenAI: 'Azure OpenAI',
  openAI: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Gemini',
  claude: 'Claude',
  ollama: 'Ollama',
  bedrock: 'AWS Bedrock',
  xai: 'xAI',
  together: 'Together',
  groq: 'Groq',
  fireworks: 'Fireworks',
  cohere: 'Cohere',
  openAICompatible: 'OpenAI API Compatible',
  mistral: 'Mistral',
  voyage: 'Voyage',
  jinaAI: 'Jina AI',
  sentenceTransformers: 'Default',
  default: 'Default',
};

export const formattedProvider = (provider: string): string =>
  PROVIDER_DISPLAY_NAMES[provider] || normalizeDisplayName(provider);
