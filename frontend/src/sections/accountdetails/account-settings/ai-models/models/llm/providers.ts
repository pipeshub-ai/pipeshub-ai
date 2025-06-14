import { z } from 'zod';
import linkIcon from '@iconify-icons/mdi/link';
import keyIcon from '@iconify-icons/mdi/key';
import cubeIcon from '@iconify-icons/mdi/cube-outline';
import serverIcon from '@iconify-icons/mdi/server';
import {
  ProviderDefinition,
  UniversalModelFactory,
  BaseFormValues,
  BaseFieldConfig,
} from '../../core/universal-form-factory';

// To add a new provider, just add one object to this array!

// First, define the provider type structure
type ProviderConfig =
  | {
      id: string;
      name: string;
      models: string;
      description?: string;
      fields?: (keyof typeof FIELD_TEMPLATES)[];
      noApiKey?: boolean;
    }
  | { id: string; name: string; description?: string; special: true };

const PROVIDERS: readonly ProviderConfig[] = [
  // Basic providers (just API key + model)
  {
    id: 'openAI',
    name: 'OpenAI API',
    description: 'Enter your OpenAI API credentials to get started.',
    models: 'gpt-4o, gpt-4-turbo, gpt-3.5-turbo',
  },
  {
    id: 'gemini',
    name: 'Gemini API',
    description: 'Enter your Gemini API credentials to get started.',
    models: 'gemini-2.0-flash',
  },
  {
    id: 'anthropic',
    name: 'Anthropic API',
    description: 'Enter your Anthropic API credentials to get started.',
    models: 'claude-3-7-sonnet-20250219',
  },
  // Providers with extra fields
  {
    id: 'azureOpenAI',
    name: 'Azure OpenAI Service',
    description: 'You need an active Azure subscription with Azure OpenAI Service enabled.',
    models: 'gpt-4, gpt-35-turbo',
    fields: ['endpoint', 'deploymentName'],
  },
  {
    id: 'openAICompatible',
    name: 'OpenAI API Compatible',
    description: 'Enter your OpenAI-compatible API credentials to get started.',
    models: 'deepseek-ai/DeepSeek-V3',
    fields: ['endpoint'],
  },
] as const;

// ===========================================
// 🔧 FIELD TEMPLATES (Pre-defined for reuse)
// ===========================================

const FIELD_TEMPLATES = {
  endpoint: {
    name: 'endpoint',
    label: 'Endpoint URL',
    placeholder: 'e.g., https://your-resource.openai.azure.com/',
    icon: linkIcon,
    required: true,
    validation: z
      .string()
      .min(1, 'Endpoint is required')
      .startsWith('http', 'Endpoint must start with http:// or https://'),
  },
  baseUrl: {
    name: 'baseUrl',
    label: 'Base URL',
    placeholder: 'http://localhost:11434',
    icon: serverIcon,
    required: true,
    validation: z.string().min(1, 'Base URL is required'),
  },
  deploymentName: {
    name: 'deploymentName',
    label: 'Deployment Name',
    placeholder: 'Your Azure OpenAI deployment name',
    icon: cubeIcon,
    required: true,
    validation: z.string().min(1, 'Deployment Name is required'),
  },
  region: {
    name: 'region',
    label: 'Region',
    placeholder: 'us-east-1',
    icon: serverIcon,
    required: true,
    validation: z.string().min(1, 'Region is required'),
  },
  accessKeyId: {
    name: 'accessKeyId',
    label: 'Access Key ID',
    type: 'password' as const,
    placeholder: 'AKIAIOSFODNN7EXAMPLE',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Access Key ID is required'),
  },
  secretAccessKey: {
    name: 'secretAccessKey',
    label: 'Secret Access Key',
    type: 'password' as const,
    placeholder: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'Secret Access Key is required'),
  },
  organizationId: {
    name: 'organizationId',
    label: 'Organization ID',
    placeholder: 'org-xxxxxxxxxxxxxxxxxxxxxxxx',
    icon: cubeIcon,
    required: false,
    validation: z.string().optional(),
  },
  projectId: {
    name: 'projectId',
    label: 'Project ID',
    placeholder: 'your-project-id',
    icon: cubeIcon,
    required: false,
    validation: z.string().optional(),
  },
} as const;

// ===========================================
// AUTO-GENERATION (Don't touch this part!)
// ===========================================

// Auto-generate TypeScript types
type ProviderId = (typeof PROVIDERS)[number]['id'];
type FieldKey = keyof typeof FIELD_TEMPLATES;

// Helper type to extract regular providers (non-special)
type RegularProvider = Extract<ProviderConfig, { models: string }>;
type SpecialProvider = Extract<ProviderConfig, { special: true }>;

// Auto-generate provider definitions
const LLM_PROVIDERS: ProviderDefinition[] = PROVIDERS.map((provider) => {
  // Handle special providers (like default)
  if ('special' in provider && provider.special) {
    return {
      id: provider.id,
      label: provider.name,
      description: provider.description,
      extraFields: [],
      customValidation: () => z.object({ modelType: z.literal(provider.id as any) }),
    };
  }

  // For regular providers, we know they have models
  const regularProvider = provider as Extract<ProviderConfig, { models: string }>;

  const base: ProviderDefinition = {
    id: regularProvider.id,
    label: regularProvider.name,
    description: regularProvider.description,
    modelPlaceholder: `e.g., ${regularProvider.models}`,
    extraFields: [],
  };

  // Add extra fields if specified
  if (regularProvider.fields) {
    base.extraFields = regularProvider.fields
      .map((fieldKey: keyof typeof FIELD_TEMPLATES) => FIELD_TEMPLATES[fieldKey])
      .filter(Boolean);
  }

  // Handle providers without API key
  if (regularProvider.noApiKey) {
    base.excludeApiKey = true;
  }

  return base;
});

// Standard fields (API key + model) for most providers
const LLM_COMMON_FIELDS: BaseFieldConfig[] = [
  {
    name: 'apiKey',
    label: 'API Key',
    type: 'password',
    placeholder: 'Your API Key',
    icon: keyIcon,
    required: true,
    validation: z.string().min(1, 'API Key is required'),
  },
  {
    name: 'model',
    label: 'Model Name',
    placeholder: 'Model name',
    icon: cubeIcon,
    required: true,
    validation: z.string().min(1, 'Model is required'),
  },
];

export const LLM_MODEL_TYPE = UniversalModelFactory.generateModelType({
  type: 'llm',
  providers: LLM_PROVIDERS,
  commonFields: LLM_COMMON_FIELDS,
});

// ===========================================
// AUTO-GENERATED TYPES
// ===========================================

export type LlmProviderType = ProviderId;

export interface BaseLlmFormValues extends BaseFormValues {
  modelType: LlmProviderType;
  apiKey?: string;
  model?: string;
  _provider?: string;
}

// This automatically generates the correct type for ANY provider configuration

type GetProviderConfig<T extends ProviderId> = Extract<(typeof PROVIDERS)[number], { id: T }>;

type GenerateFormValues<T extends ProviderId> =
  GetProviderConfig<T> extends SpecialProvider
    ? { modelType: T; _provider?: string }
    : GetProviderConfig<T> extends RegularProvider & { noApiKey: true }
      ? {
          modelType: T;
          model: string;
          _provider?: string;
        } & {
          [K in GetProviderConfig<T> extends { fields: readonly (infer F)[] }
            ? F extends keyof typeof FIELD_TEMPLATES
              ? F
              : never
            : never]: string;
        }
      : {
          modelType: T;
          apiKey: string;
          model: string;
          _provider?: string;
        } & {
          [K in GetProviderConfig<T> extends { fields: readonly (infer F)[] }
            ? F extends keyof typeof FIELD_TEMPLATES
              ? F
              : never
            : never]: string;
        };

export type LlmFormValues = {
  [K in LlmProviderType]: GenerateFormValues<K>;
}[LlmProviderType];

// Export functions
export const getLlmProviders = () => LLM_MODEL_TYPE.providers;
export const getLlmProviderById = (id: string) =>
  UniversalModelFactory.getProviderById(LLM_MODEL_TYPE.providers, id);

// ===========================================
// HOW TO ADD NEW PROVIDERS
// ===========================================

/*

🚀 ADDING A NEW PROVIDER IS SUPER EASY!

Just add ONE line to the PROVIDERS array above - THAT'S IT!
TypeScript types are 100% AUTO-GENERATED from your configuration!

1. BASIC PROVIDER (just API key + model):
   { id: 'cohere', name: 'Cohere', models: 'command-r-plus', description: 'Enter your Cohere API credentials.' },
   → Auto-generates: { modelType: 'cohere', apiKey: string, model: string }

2. PROVIDER WITH EXTRA FIELDS:
   { id: 'perplexity', name: 'Perplexity', models: 'llama-3.1-sonar-large-128k-online', fields: ['endpoint'] },
   → Auto-generates: { modelType: 'perplexity', apiKey: string, model: string, endpoint: string }

3. PROVIDER WITHOUT API KEY:
   { id: 'ollama', name: 'Ollama', models: 'llama2, mistral', fields: ['baseUrl'], noApiKey: true },
   → Auto-generates: { modelType: 'ollama', model: string, baseUrl: string }

4. SPECIAL PROVIDER:
   { id: 'default', name: 'Default (System Provided)', special: true },
   → Auto-generates: { modelType: 'default' }

🎉 NO NEED TO TOUCH GenerateFormValues OR ANY OTHER TYPE DEFINITIONS!
Everything is automatically inferred from your PROVIDERS configuration!

REAL EXAMPLES:
=============

// Add these to PROVIDERS array:

// Basic providers
{ id: 'cohere', name: 'Cohere', models: 'command-r-plus', description: 'Enter your Cohere API credentials.' },
{ id: 'perplexity', name: 'Perplexity AI', models: 'llama-3.1-sonar-large-128k-online', description: 'Enter your Perplexity API credentials.' },

// With endpoint
{ id: 'together', name: 'Together AI', models: 'meta-llama/Llama-2-70b-chat-hf', fields: ['endpoint'] },

// Multiple fields  
{ id: 'bedrock', name: 'AWS Bedrock', models: 'anthropic.claude-v2', fields: ['region', 'accessKeyId', 'secretAccessKey'] },

// No API key needed
{ id: 'ollama', name: 'Ollama', models: 'llama2, mistral', fields: ['baseUrl'], noApiKey: true },

*/
