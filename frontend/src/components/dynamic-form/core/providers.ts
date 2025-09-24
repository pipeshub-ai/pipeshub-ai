import { z } from 'zod';
import { FIELD_TEMPLATES, FieldTemplate, FieldTemplateName } from './field-templates';

export interface FieldConfig {
  name: FieldTemplateName;
  required?: boolean; // Optional, defaults to template's required value
  defaultValue?: any;
  placeholder?: string; // Optional placeholder text for the field
}

export interface ProviderConfig {
  id: string;
  label: string;
  description?: string;
  modelPlaceholder?: string;
  fields?: readonly (FieldTemplateName | FieldConfig)[];
  customFields?: Record<string, Partial<FieldTemplate>>;
  customValidation?: (fields: any) => z.ZodType;
  isSpecial?: boolean;
  accountType?: 'individual' | 'business';
}

export const createUrlValidator = (optional: boolean = true) => {
  const baseValidator = z.string().refine(
    (val) => {
      if (!val || val.trim() === '') return optional;
      try {
        const url = new URL(val);
        return !!url;
      } catch {
        return false;
      }
    },
    { message: 'Must be a valid URL' }
  );

  return optional ? baseValidator.optional().or(z.literal('')) : baseValidator;
};

export const URL_VALIDATOR = createUrlValidator(true);

export const ENHANCED_FIELD_TEMPLATES = {
  ...FIELD_TEMPLATES,
  baseUrl: {
    name: 'baseUrl',
    label: 'Base URL (Optional)',
    type: 'url' as const,
    placeholder: 'http://localhost:3000/files',
    icon: 'linkIcon', // Assuming you import this
    required: false,
    validation: URL_VALIDATOR, // ✅ Use the reusable validator
    gridSize: { xs: 12, sm: 6 },
  },
} as const;

// LLM PROVIDERS
export const LLM_PROVIDERS: readonly ProviderConfig[] = [
  {
    id: 'openAI',
    label: 'OpenAI API',
    description: 'Enter your OpenAI API credentials to get started.',
    modelPlaceholder: 'e.g., gpt-4o-mini, gpt-4o',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'gemini',
    label: 'Gemini API',
    description: 'Enter your Gemini API credentials to get started.',
    modelPlaceholder: 'e.g., gemini-2.5-flash',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'anthropic',
    label: 'Anthropic API',
    description: 'Enter your Anthropic API credentials to get started.',
    modelPlaceholder: 'e.g., claude-3-7-sonnet-20250219',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'azureOpenAI',
    label: 'Azure OpenAI',
    description: 'You need an active Azure subscription with Azure OpenAI Service enabled.',
    modelPlaceholder: 'e.g., gpt-4o-mini, gpt-4o',
    fields: [
      'endpoint',
      'apiKey',
      'deploymentName',
      'model',
      { name: 'isMultimodal', required: false, defaultValue: true },
    ],
    customFields: {
      endpoint: {
        placeholder: 'e.g., https://your-resource.openai.azure.com/',
      },
    },
  },
  {
    id: 'ollama',
    label: 'Ollama',
    description: 'Connect to your local Ollama instance.',
    modelPlaceholder: 'e.g., llama2, codellama, mistral',
    fields: [
      { name: 'model', required: true },
      { name: 'apiKey', required: false }, // API key is optional for Ollama
      {
        name: 'endpoint',
        required: false,
        defaultValue: 'http://host.docker.internal:11434',
        placeholder: 'e.g.http://localhost:11434',
      }, // Optional endpoint
      { name: 'isMultimodal', required: false, defaultValue: true },
    ],
  },
  {
    id: 'bedrock',
    label: 'AWS Bedrock',
    description: 'Enter your AWS Bedrock API credentials to get started.',
    modelPlaceholder:
      'e.g. us.anthropic.claude-sonnet-4-20250514-v1:0, arn:aws:bedrock:us-east-1:106782021127:inference-profile/apac.anthropic.claude-sonnet-4-20250514-v1:0"',
    fields: [
      { name: 'awsAccessKeyId', required: true },
      { name: 'awsAccessSecretKey', required: true },
      { name: 'region', required: true },
      { name: 'model', required: true, placeholder: 'model id/arn' },
      { name: 'provider', required: true, defaultValue: 'anthropic' },
      { name: 'isMultimodal', required: false, defaultValue: true },
    ],
  },
  {
    id: 'xai',
    label: 'xAI',
    description: 'Enter your XAI API credentials to get started.',
    modelPlaceholder: 'e.g. grok-3-latest',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'mistral',
    label: 'Mistral',
    description: 'Enter your Mistral API credentials to get started.',
    modelPlaceholder: 'e.g. mistral-large-latest',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'together',
    label: 'Together',
    description: 'Enter your Together API credentials to get started.',
    modelPlaceholder: 'e.g., deepseek-ai/DeepSeek-V3',
    fields: [
      'apiKey',
      'model',
      'endpoint',
      { name: 'isMultimodal', required: false, defaultValue: true },
    ],
    customFields: {
      endpoint: {
        placeholder: 'e.g., https://api.together.xyz/v1',
      },
    },
  },
  {
    id: 'groq',
    label: 'Groq',
    description: 'Enter your Groq API credentials to get started.',
    modelPlaceholder: 'e.g. meta-llama/llama-4-scout-17b-16e-instruct',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'fireworks',
    label: 'Fireworks',
    description: 'Enter your Fireworks API credentials to get started.',
    modelPlaceholder: 'e.g. accounts/fireworks/models/kimi-k2-instruct',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'cohere',
    label: 'Cohere',
    description: 'Enter your Cohere API credentials to get started.',
    modelPlaceholder: 'e.g. command-a-03-2025',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: true }],
  },
  {
    id: 'openAICompatible',
    label: 'OpenAI API Compatible',
    description: 'Enter your OpenAI-compatible API credentials to get started.',
    modelPlaceholder: 'e.g. deepseek-ai/DeepSeek-V3',
    fields: [
      'endpoint',
      'apiKey',
      'model',
      { name: 'isMultimodal', required: false, defaultValue: true },
    ],
    customFields: {
      endpoint: {
        placeholder: 'e.g., https://api.together.xyz/v1/',
      },
    },
  },
] as const;

// EMBEDDING PROVIDERS
export const EMBEDDING_PROVIDERS: readonly ProviderConfig[] = [
  {
    id: 'default',
    label: 'Default (System Provided)',
    description:
      'Using the default embedding model provided by the system. No additional configuration required.',
    isSpecial: true,
  },
  {
    id: 'openAI',
    label: 'OpenAI API',
    description: 'Enter your OpenAI API credentials for embeddings.',
    modelPlaceholder: 'e.g., text-embedding-3-small, text-embedding-3-large',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: false }],
  },
  {
    id: 'gemini',
    label: 'Gemini API',
    description: 'Enter your Gemini API credentials for embeddings.',
    modelPlaceholder: 'e.g., gemini-embedding-001',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: false }],
  },
  {
    id: 'cohere',
    label: 'Cohere',
    description: 'Enter your Cohere API credentials for embeddings.',
    modelPlaceholder: 'e.g., embed-v4.0',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: false }],
  },
  {
    id: 'azureOpenAI',
    label: 'Azure OpenAI',
    description: 'Configure Azure OpenAI for embeddings.',
    modelPlaceholder: 'e.g., text-embedding-3-small',
    fields: [
      'endpoint',
      'apiKey',
      'deploymentName',
      'model',
      { name: 'isMultimodal', required: false, defaultValue: false },
    ],
    customFields: {
      endpoint: {
        placeholder: 'e.g., https://your-resource.openai.azure.com/',
      },
    },
  },
  {
    id: 'sentenceTransformers',
    label: 'Sentence Transformers',
    description: 'Use local Sentence Transformers models (no API key required).',
    modelPlaceholder: 'e.g., all-MiniLM-L6-v2',
    fields: ['model', { name: 'isMultimodal', required: false, defaultValue: false }],
  },
  {
    id: 'ollama',
    label: 'Ollama',
    description: 'Connect to your local Ollama instance.',
    modelPlaceholder: 'e.g., mxbai-embed-large',
    fields: [
      { name: 'model', required: true },
      {
        name: 'endpoint',
        required: false,
        defaultValue: 'http://host.docker.internal:11434',
        placeholder: 'e.g. http://localhost:11434',
      }, // Optional endpoint
      { name: 'isMultimodal', required: false, defaultValue: false },
    ],
  },
  {
    id: 'bedrock',
    label: 'AWS Bedrock',
    description: 'Enter your AWS Bedrock API credentials for embeddings.',
    modelPlaceholder: 'e.g. cohere2.embed-multilingual-v3',
    fields: [
      { name: 'awsAccessKeyId', required: true },
      { name: 'awsAccessSecretKey', required: true },
      { name: 'region', required: true },
      { name: 'model', required: true, placeholder: 'Model ID/ARN' },
      { name: 'provider', required: true, defaultValue: 'cohere' },
      { name: 'isMultimodal', required: false, defaultValue: false },
    ],
  },
  {
    id: 'jinaAI',
    label: 'Jina AI',
    description: 'Enter your Jina AI API credentials for embeddings.',
    modelPlaceholder: 'e.g., jina-embeddings-v3',
    fields: [
      { name: 'model', required: true },
      { name: 'apiKey', required: true },
      { name: 'isMultimodal', required: false, defaultValue: false },
    ],
  },
  {
    id: 'mistral',
    label: 'Mistral',
    description: 'Enter your Mistral API credentials for embeddings.',
    modelPlaceholder: 'e.g., mistral-embed',
    fields: ['apiKey', 'model', { name: 'isMultimodal', required: false, defaultValue: false }],
  },
  {
    id: 'voyage',
    label: 'Voyage',
    description: 'Enter your Voyage API credentials for embeddings.',
    modelPlaceholder: 'e.g., voyage-3.5',
    fields: [
      { name: 'model', required: true },
      { name: 'apiKey', required: true },
      { name: 'isMultimodal', required: false, defaultValue: false },
    ],
  },
] as const;

// STORAGE PROVIDERS
export const STORAGE_PROVIDERS: readonly ProviderConfig[] = [
  {
    id: 'local',
    label: 'Local Storage',
    description: 'Store files locally on the server. Additional options are optional.',
    fields: ['mountName', 'baseUrl'],
    customValidation: () =>
      z.object({
        providerType: z.literal('local'),
        modelType: z.literal('local'),
      }),
  },
  {
    id: 's3',
    label: 'Amazon S3',
    description: 'Configure Amazon S3 storage for your application data.',
    fields: ['s3AccessKeyId', 's3SecretAccessKey', 's3Region', 's3BucketName'],
  },
  {
    id: 'azureBlob',
    label: 'Azure Blob Storage',
    description: 'Configure Azure Blob Storage for your application data.',
    fields: ['accountName', 'accountKey', 'containerName', 'endpointProtocol', 'endpointSuffix'],
  },
] as const;

// SMTP PROVIDERS
export const SMTP_PROVIDERS: readonly ProviderConfig[] = [
  {
    id: 'smtp',
    label: 'SMTP Configuration',
    description: 'Configure SMTP settings for email notifications.',
    fields: ['host', 'port', 'fromEmail', 'username', 'password'],
  },
] as const;

// URL PROVIDERS
export const URL_PROVIDERS: readonly ProviderConfig[] = [
  {
    id: 'urls',
    label: 'Public URLs',
    description: 'Configure the public URLs for your services.',
    fields: ['frontendUrl', 'connectorUrl'],
  },
] as const;
