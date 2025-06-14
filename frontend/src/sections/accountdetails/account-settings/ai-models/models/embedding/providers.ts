import { z } from 'zod';
import linkIcon from '@iconify-icons/mdi/link';
import keyIcon from '@iconify-icons/mdi/key';
import cubeIcon from '@iconify-icons/mdi/cube-outline';
import serverIcon from '@iconify-icons/mdi/server';
import { 
  ProviderDefinition, 
  UniversalModelFactory,
  BaseFormValues,
  BaseFieldConfig
} from '../../core/universal-form-factory';

// To add a new provider, just add one object to this array!

// First, define the provider type structure
type ProviderConfig = 
  | { id: string; name: string; models: string; fields?: (keyof typeof FIELD_TEMPLATES)[]; noApiKey?: boolean }
  | { id: string; name: string; special: true };

const PROVIDERS: readonly ProviderConfig[] = [
  // Basic providers (just API key + model)
  { id: 'openAI', name: 'OpenAI API', models: 'text-embedding-3-small, text-embedding-3-large' },
  { id: 'gemini', name: 'Gemini API', models: 'gemini-embedding-exp-03-07' },
  { id: 'cohere', name: 'Cohere', models: 'embed-v4.0' },  
  // Providers with extra fields
  { id: 'azureOpenAI', name: 'Azure OpenAI Service', models: 'text-embedding-3-small', fields: ['endpoint', 'deploymentName'] },

  // Providers without API key
  { id: 'sentenceTransformers', name: 'Sentence Transformers', models: 'all-MiniLM-L6-v2', noApiKey: true },
  
  // Special providers
  { id: 'default', name: 'Default (System Provided)', special: true },
] as const;


const FIELD_TEMPLATES = {
  endpoint: {
    name: 'endpoint',
    label: 'Endpoint URL',
    placeholder: 'https://api.example.com/v1',
    icon: linkIcon,
    required: true,
    validation: z.string().min(1, 'Endpoint is required').url('Must be a valid URL'),
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
    placeholder: 'Your deployment name',
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
} as const;


// AUTO-GENERATION (Don't touch this part!)

// Auto-generate TypeScript types
type ProviderId = typeof PROVIDERS[number]['id'];
type FieldKey = keyof typeof FIELD_TEMPLATES;

// Helper type to extract regular providers (non-special)
type RegularProvider = Extract<ProviderConfig, { models: string }>;
type SpecialProvider = Extract<ProviderConfig, { special: true }>;

// Auto-generate provider definitions
const EMBEDDING_PROVIDERS: ProviderDefinition[] = PROVIDERS.map(provider => {
  // Handle special providers (like default)
  if ('special' in provider && provider.special) {
    return {
      id: provider.id,
      label: provider.name,
      extraFields: [],
      customValidation: () => z.object({ modelType: z.literal(provider.id as any) }),
    };
  }

  // For regular providers, we know they have models
  const regularProvider = provider as Extract<ProviderConfig, { models: string }>;
  
  const base: ProviderDefinition = {
    id: regularProvider.id,
    label: regularProvider.name,
    modelPlaceholder: `e.g., ${regularProvider.models}`,
    extraFields: [],
  };

  // Add extra fields if specified
  if (regularProvider.fields) {
    base.extraFields = regularProvider.fields.map((fieldKey: keyof typeof FIELD_TEMPLATES) => 
      FIELD_TEMPLATES[fieldKey]
    ).filter(Boolean);
  }

  // Handle providers without API key
  if (regularProvider.noApiKey) {
    // 🔥 FIX: Exclude only the apiKey field, keep model field
    base.excludeApiKey = true;
    
    base.customValidation = (fields) => {
      // Only include modelType, model, and extra fields (no apiKey)
      const allowedFields: Record<string, any> = {
        modelType: fields.modelType,
        model: z.string().min(1, 'Model is required') // Keep model field
      };
      
      // Add extra fields
      if (regularProvider.fields) {
        regularProvider.fields.forEach(fieldKey => {
          const fieldTemplate = FIELD_TEMPLATES[fieldKey as keyof typeof FIELD_TEMPLATES];
          if (fieldTemplate && fieldTemplate.validation) {
            allowedFields[fieldKey] = fieldTemplate.validation;
          }
        });
      }
      
      return z.object(allowedFields);
    };
  }

  return base;
});

// Standard fields (API key + model) for most providers
const EMBEDDING_COMMON_FIELDS: BaseFieldConfig[] = [
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

export const EMBEDDING_MODEL_TYPE = UniversalModelFactory.generateModelType({
  type: 'embedding',
  providers: EMBEDDING_PROVIDERS,
  commonFields: EMBEDDING_COMMON_FIELDS,
});

export type EmbeddingProviderType = ProviderId;

export interface BaseEmbeddingFormValues extends BaseFormValues {
  modelType: EmbeddingProviderType;
  apiKey?: string;
  model?: string;
  _provider?: string;
}

// This automatically generates the correct type for ANY provider configuration

type GetProviderConfig<T extends ProviderId> = Extract<typeof PROVIDERS[number], { id: T }>;

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
          : never]: string
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
          : never]: string
      };

export type EmbeddingFormValues = {
  [K in EmbeddingProviderType]: GenerateFormValues<K>
}[EmbeddingProviderType];

// Export functions
export const getEmbeddingProviders = () => EMBEDDING_MODEL_TYPE.providers;
export const getEmbeddingProviderById = (id: string) => 
  UniversalModelFactory.getProviderById(EMBEDDING_MODEL_TYPE.providers, id);

// HOW TO ADD NEW PROVIDERS

/*

🚀 ADDING A NEW PROVIDER IS SUPER EASY!

Just add ONE line to the PROVIDERS array above - THAT'S IT!
TypeScript types are 100% AUTO-GENERATED from your configuration!

1. BASIC PROVIDER (just API key + model):
   { id: 'mistral', name: 'Mistral AI', models: 'mistral-embed' },
   → Auto-generates: { modelType: 'mistral', apiKey: string, model: string }

2. PROVIDER WITH EXTRA FIELDS:
   { id: 'replicate', name: 'Replicate', models: 'replicate/all-mpnet-base-v2', fields: ['endpoint'] },
   → Auto-generates: { modelType: 'replicate', apiKey: string, model: string, endpoint: string }

3. PROVIDER WITHOUT API KEY:
   { id: 'local', name: 'Local Model', models: 'local-embedding-model', fields: ['baseUrl'], noApiKey: true },
   → Auto-generates: { modelType: 'local', model: string, baseUrl: string }

4. SPECIAL PROVIDER:
   { id: 'default', name: 'Default (System Provided)', special: true },
   → Auto-generates: { modelType: 'default' }

5. NEED A NEW FIELD TYPE? Add it to FIELD_TEMPLATES first:
   projectId: {
     name: 'projectId',
     label: 'Project ID',
     placeholder: 'your-project-id',
     icon: cubeIcon,
     required: true,
     validation: z.string().min(1, 'Project ID is required'),
   },

Then use it in any provider:
   { id: 'vertexai', name: 'Google Vertex AI', models: 'textembedding-gecko', fields: ['region', 'projectId'] },
   → Auto-generates: { modelType: 'vertexai', apiKey: string, model: string, region: string, projectId: string }

🎉 NO NEED TO TOUCH GenerateFormValues OR ANY OTHER TYPE DEFINITIONS!
Everything is automatically inferred from your PROVIDERS configuration!

REAL EXAMPLES:
=============

// Add these to PROVIDERS array:

// Basic providers
{ id: 'mistral', name: 'Mistral AI', models: 'mistral-embed' },
{ id: 'jina', name: 'Jina AI', models: 'jina-embeddings-v2-base-en' },

// With endpoint
{ id: 'replicate', name: 'Replicate', models: 'replicate/all-mpnet-base-v2', fields: ['endpoint'] },

// Multiple fields  
{ id: 'vertexai', name: 'Google Vertex AI', models: 'textembedding-gecko', fields: ['region', 'projectId'] },

// No API key needed
{ id: 'fastembed', name: 'FastEmbed', models: 'BAAI/bge-small-en', fields: ['baseUrl'], noApiKey: true },

*/