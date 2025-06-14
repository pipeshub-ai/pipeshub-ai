import { z } from 'zod';
import { FIELD_TEMPLATES } from './field-templates';

// LLM PROVIDERS
export const LLM_PROVIDERS = [
  {
    id: 'openAI',
    label: 'OpenAI API',
    description: 'Enter your OpenAI API credentials to get started.',
    modelPlaceholder: 'e.g., gpt-4o, gpt-4-turbo, gpt-3.5-turbo',
    fields: ['apiKey', 'model'] as const,
  },
  {
    id: 'gemini',
    label: 'Gemini API',
    description: 'Enter your Gemini API credentials to get started.',
    modelPlaceholder: 'e.g., gemini-2.0-flash',
    fields: ['apiKey', 'model'] as const,
  },
  {
    id: 'anthropic',
    label: 'Anthropic API',
    description: 'Enter your Anthropic API credentials to get started.',
    modelPlaceholder: 'e.g., claude-3-7-sonnet-20250219',
    fields: ['apiKey', 'model'] as const,
  },
  {
    id: 'azureOpenAI',
    label: 'Azure OpenAI Service',
    description: 'You need an active Azure subscription with Azure OpenAI Service enabled.',
    modelPlaceholder: 'e.g., gpt-4, gpt-35-turbo',
    fields: ['endpoint', 'apiKey', 'deploymentName', 'model'] as const,
    customFields: {
      endpoint: {
        ...FIELD_TEMPLATES.endpoint,
        placeholder: 'e.g., https://your-resource.openai.azure.com/',
      },
    },
  },
  {
    id: 'openAICompatible',
    label: 'OpenAI API Compatible',
    description: 'Enter your OpenAI-compatible API credentials to get started.',
    modelPlaceholder: 'e.g., deepseek-ai/DeepSeek-V3',
    fields: ['endpoint', 'apiKey', 'model'] as const,
    customFields: {
      endpoint: {
        ...FIELD_TEMPLATES.endpoint,
        placeholder: 'e.g., https://api.together.xyz/v1/',
      },
    },
  },
] as const;

// EMBEDDING PROVIDERS
export const EMBEDDING_PROVIDERS = [
  {
    id: 'openAI',
    label: 'OpenAI API',
    description: 'Enter your OpenAI API credentials for embeddings.',
    modelPlaceholder: 'e.g., text-embedding-3-small, text-embedding-3-large',
    fields: ['apiKey', 'model'] as const,
  },
  {
    id: 'gemini',
    label: 'Gemini API',
    description: 'Enter your Gemini API credentials for embeddings.',
    modelPlaceholder: 'e.g., gemini-embedding-exp-03-07',
    fields: ['apiKey', 'model'] as const,
  },
  {
    id: 'cohere',
    label: 'Cohere',
    description: 'Enter your Cohere API credentials for embeddings.',
    modelPlaceholder: 'e.g., embed-v4.0',
    fields: ['apiKey', 'model'] as const,
  },
  {
    id: 'azureOpenAI',
    label: 'Azure OpenAI Service',
    description: 'Configure Azure OpenAI for embeddings.',
    modelPlaceholder: 'e.g., text-embedding-3-small',
    fields: ['endpoint', 'apiKey', 'model'] as const,
    customFields: {
      endpoint: {
        ...FIELD_TEMPLATES.endpoint,
        placeholder: 'e.g., https://your-resource.openai.azure.com/',
      },
    },
  },
  {
    id: 'sentenceTransformers',
    label: 'Sentence Transformers',
    description: 'Use local Sentence Transformers models (no API key required).',
    modelPlaceholder: 'e.g., all-MiniLM-L6-v2',
    fields: ['model'] as const,
  },
  {
    id: 'default',
    label: 'Default (System Provided)',
    description:
      'Using the default embedding model provided by the system. No additional configuration required.',
    isSpecial: true,
  },
] as const;

// STORAGE PROVIDERS
export const STORAGE_PROVIDERS = [
  {
    id: 'local',
    label: 'Local Storage',
    description: 'Store files locally on the server. Additional options are optional.',
    fields: ['mountName', 'baseUrl'] as const,
    // Custom validation to allow empty optional fields
    customValidation: (fields: any) =>
      z
        .object({
          providerType: z.literal('local'),
          modelType: z.literal('local'),
          mountName: z.string().optional().or(z.literal('')),
          baseUrl: z
            .string()
            .optional()
            .or(z.literal(''))
            .refine(
              (val) => {
                // Allow empty string or valid URL
                if (!val || val.trim() === '') return true;
                try {
                  const url = new URL(val);
                  return !!url;
                } catch {
                  return false;
                }
              },
              { message: 'Must be a valid URL' }
            ),
        })
        .refine((data) => true, { message: 'Local storage configuration is valid' }),
  },
  {
    id: 's3',
    label: 'Amazon S3',
    description: 'Configure Amazon S3 storage for your application data.',
    fields: ['s3AccessKeyId', 's3SecretAccessKey', 's3Region', 's3BucketName'] as const,
  },
  {
    id: 'azureBlob',
    label: 'Azure Blob Storage',
    description: 'Configure Azure Blob Storage for your application data.',
    fields: [
      'accountName',
      'accountKey',
      'containerName',
      'endpointProtocol',
      'endpointSuffix',
    ] as const,
  },
] as const;

// SMTP PROVIDERS
export const SMTP_PROVIDERS = [
  {
    id: 'smtp',
    label: 'SMTP Configuration',
    description: 'Configure SMTP settings for email notifications.',
    fields: ['host', 'port', 'fromEmail', 'username', 'password'] as const,
  },
] as const;

// URL PROVIDERS
export const URL_PROVIDERS = [
  {
    id: 'urls',
    label: 'Public URLs',
    description: 'Configure the public URLs for your services.',
    fields: ['frontendUrl', 'connectorUrl'] as const,
  },
] as const;

// HOW TO ADD NEW PROVIDERS

/*

🚀 ADDING A NEW PROVIDER IS SUPER EASY!

Just add ONE object to the appropriate array above!

EXAMPLES:

1. New LLM Provider:
   {
     id: 'cohere',
     label: 'Cohere',
     description: 'Enter your Cohere API credentials to get started.',
     modelPlaceholder: 'e.g., command-r-plus',
     fields: ['apiKey', 'model'] as const,
   },

2. New Storage Provider:
   {
     id: 'gcs',
     label: 'Google Cloud Storage',
     description: 'Configure Google Cloud Storage for your application data.',
     fields: ['gcsProjectId', 'gcsBucketName', 'gcsCredentials'] as const,
   },

3. New Field Template (add to FIELD_TEMPLATES first):
   gcsProjectId: {
     name: 'gcsProjectId',
     label: 'Project ID',
     placeholder: 'your-project-id',
     icon: keyIcon,
     required: true,
     validation: z.string().min(1, 'Project ID is required'),
   },

   THAT'S IT! Everything else is auto-generated:
*/
