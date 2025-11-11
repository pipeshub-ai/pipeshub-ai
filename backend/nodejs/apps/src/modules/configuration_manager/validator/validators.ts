import { z } from 'zod';
import { googleWorkspaceTypes, storageTypes } from '../constants/constants';

export const baseStorageSchema = z.object({
  storageType: z.enum([
    storageTypes.LOCAL,
    storageTypes.S3,
    storageTypes.AZURE_BLOB,
  ]),
});

export const s3ConfigSchema = baseStorageSchema.extend({
  storageType: z.literal(storageTypes.S3),
  s3AccessKeyId: z.string().min(1, { message: 'S3 access key ID is required' }),
  s3SecretAccessKey: z.string().min(1, {
    message: 'S3 secret access key is required',
  }),
  s3Region: z.string().min(1, { message: 'S3 region is required' }),
  s3BucketName: z.string().min(1, { message: 'S3 bucket name is required' }),
});

export const azureBlobConfigSchema = baseStorageSchema.extend({
  storageType: z.literal(storageTypes.AZURE_BLOB),
  // Option 1: Connection string approach
  azureBlobConnectionString: z.string().optional(),
  // Option 2: Individual parameter approach
  endpointProtocol: z.enum(['http', 'https']).optional().default('https'),
  accountName: z
    .string()
    .min(1, { message: 'Azure account name is required' })
    .optional(),
  accountKey: z
    .string()
    .min(1, { message: 'Azure account key is required' })
    .optional(),
  endpointSuffix: z
    .string()
    .min(1, { message: 'Azure endpoint suffix is required' })
    .optional()
    .default('core.windows.net'),
  containerName: z
    .string()
    .min(1, { message: 'Azure container name is required' }),
});

export const azureBlobConfigSchemaRefined = azureBlobConfigSchema.refine(
  (data) => {
    const option1Params =
      !!data.azureBlobConnectionString && !!data.containerName;
    const option2Params = !!(
      data.accountName &&
      data.accountKey &&
      data.containerName
    );
    return option1Params || option2Params;
  },
  {
    message:
      'You must provide either a (connection string OR all of these parameters: accountName, accountKey), and containerName',
    path: ['body'],
  },
);

export const localConfigSchema = baseStorageSchema.extend({
  storageType: z.literal(storageTypes.LOCAL),
  // You can add local storage specific fields here if needed
  mountName: z.string().optional(),
  baseUrl: z.string().url().optional(),
});

export const storageValidationSchema = z.object({
  body: z.discriminatedUnion('storageType', [
    s3ConfigSchema,
    azureBlobConfigSchema,
    localConfigSchema,
  ]),
});

export const smtpConfigSchema = z.object({
  body: z.object({
    host: z.string().min(1, { message: 'SMTP host is required' }),
    port: z.number().min(1, { message: 'SMTP port is required' }),
    username: z.string().optional(),
    password: z.string().optional(),
    fromEmail: z.string().min(1, { message: 'SMTP from is required' }),
  }),
});

export const azureAdConfigSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Azure client ID is required' }),
    tenantId: z.string().optional().default('common'),
  }),
});

export const ssoConfigSchema = z.object({
  body: z.object({
    entryPoint: z.string().min(1, { message: 'SSO entry point is required' }),
    certificate: z.string().min(1, { message: 'SSO certificate is required' }),
    emailKey: z.string().min(1, { message: 'SSO Email Key is required' }),
  }),
});

export const googleAuthConfigSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Google client ID is required' }),
  }),
});

export const oauthConfigSchema = z.object({
  body: z.object({
    providerName: z.string().min(1, { message: 'Provider name is required' }),
    clientId: z.string().min(1, { message: 'Client ID is required' }),
    clientSecret: z.string().optional(),
    authorizationUrl: z.string().url().optional().or(z.literal('')),
    tokenEndpoint: z.string().url().optional().or(z.literal('')),
    userInfoEndpoint: z.string().url().optional().or(z.literal('')),
    scope: z.string().optional(),
    redirectUri: z.string().url().optional().or(z.literal('')),
  }),
});

export const microsoftConfigSchema = z.object({
  clientId: z.string().min(1, { message: 'Microsoft client ID is required' }),
  tenantId: z.string().optional().default('common'),
});

export const mongoDBConfigSchema = z.object({
  body: z.object({
    uri: z.string().url(),
  }),
});

export const arangoDBConfigSchema = z.object({
  body: z.object({
    url: z.string().url(),
    username: z.string().optional(),
    password: z.string().optional(),
  }),
});

// Unified database configuration schema
export const dbConfigSchema = z.object({
  body: z
    .object({
      mongodb: mongoDBConfigSchema.optional(),
      arangodb: arangoDBConfigSchema.optional(),
    })
    .refine(
      (data) => {
        // Count how many database configs are provided
        const configCount = [data.mongodb, data.arangodb].filter(
          Boolean,
        ).length;

        // Ensure at least one database configuration is provided
        return configCount >= 1;
      },
      {
        message:
          "At least one database configuration must be provided: 'mongodb' and/or 'arangodb'",
        path: ['body'],
      },
    ),
});

export const redisConfigSchema = z.object({
  body: z.object({
    host: z.string().min(1, { message: 'Redis host is required' }),
    port: z.number().min(1, { message: 'Redis port is required' }),
    password: z.string().optional(),
    tls: z.boolean().optional(),
  }),
});

export const qdrantConfigSchema = z.object({
  body: z.object({
    host: z.string().min(1, { message: 'Qdrant host is required' }),
    port: z.number().min(1, { message: 'Qdrant Port is required' }),
    grpcPort: z.number().optional(),
    apiKey: z.string().optional(),
  }),
});

// Platform settings schema (feature flags and upload limits)
export const platformSettingsSchema = z.object({
  body: z.object({
    fileUploadMaxSizeBytes: z
      .number()
      .int()
      .positive()
      .max(1024 * 1024 * 1024, { message: 'Max 1GB per file' }) // safety upper bound
      .describe('Max per-file upload size in bytes'),
    featureFlags: z
      .record(z.boolean())
      .default({})
      .describe('Feature flags map, e.g., { ENABLE_WORKFLOW_BUILDER: true }'),
  }),
});

export const kafkaConfigSchema = z.object({
  body: z.object({
    brokers: z.array(z.string().url()), // Ensures an array of valid URLs
    sasl: z
      .object({
        mechanism: z.string(),
        username: z.string(),
        password: z.string(),
      })
      .optional(),
  }),
});

export const googleWorkspaceBusinessCredentialsSchema = z.object({
  type: z.string().min(1, { message: 'Account type is required' }),
  project_id: z
    .string()
    .min(1, { message: 'Google workspace project ID is required' }),
  private_key_id: z
    .string()
    .min(1, { message: 'Google workspace private key ID is required' }),
  private_key: z
    .string()
    .min(1, { message: 'Google workspace private key is required' }),
  client_email: z
    .string()
    .min(1, { message: 'Google workspace client email is required' }),
  client_id: z
    .string()
    .min(1, { message: 'Google workspace client ID is required' }),
  auth_uri: z
    .string()
    .min(1, { message: 'Google workspace auth URI is required' }),
  token_uri: z
    .string()
    .min(1, { message: 'Google workspace token URI is required' }),
  auth_provider_x509_cert_url: z.string().min(1, {
    message: 'Google workspace auth provider X509 certificate URL is required',
  }),
  client_x509_cert_url: z.string().min(1, {
    message: 'Google workspace client X509 certificate URL is required',
  }),
  universe_domain: z
    .string()
    .min(1, { message: 'Google workspace universe domain is required' }),
});

export const googleWorkspaceIndividualCredentialsSchema = z.object({
  access_token: z
    .string()
    .min(1, { message: 'Google workspace access token is required' }),
  refresh_token: z
    .string()
    .min(1, { message: 'Google workspace refresh token is required' }),
  access_token_expiry_time: z.number().min(1, {
    message: 'Google workspace access token expiry time is required',
  }),
  refresh_token_expiry_time: z.number().optional(),
});

export const googleWorkspaceCredentialsSchema = z.object({
  body: z.object({
    userType: z.enum([
      googleWorkspaceTypes.BUSINESS,
      googleWorkspaceTypes.INDIVIDUAL,
    ]),
  }),
  files: z.record(
    z.string(),
    z
      .any()
      .refine(
        (file) => file.mimetype === 'application/json',
        'All configuration files must be JSON files',
      ),
  ),
});
export const googleWorkspaceConfigSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Google client ID is required' }),
    clientSecret: z
      .string()
      .min(1, { message: 'Google client Secret is required' }),
    enableRealTimeUpdates: z.union([z.boolean(), z.string()]).optional(),
    topicName: z.string().optional(),
  }),
});

export const atlassianCredentialsSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Atlassian client ID is required' }),
    clientSecret: z.string().min(1, { message: 'Atlassian client Secret is required' }),
  }),
});

export const microsoftConnectorCredentialsSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Client ID is required' }),
    clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
    tenantId: z.string().min(1, { message: 'Tenant ID is required' }),
    hasAdminConsent: z.boolean().optional(),
  }),
});

export const sharepointCredentialsSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Client ID is required' }),
    clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
    tenantId: z.string().min(1, { message: 'Tenant ID is required' }),
    sharepointDomain: z.string().min(1, { message: 'SharePoint Domain is required' }),
    hasAdminConsent: z.boolean().optional(),
  }),
});

export const onedriveCredentialsSchema = microsoftConnectorCredentialsSchema;



// export const aiModelsConfigSchema = z.object({
//   body: z
//     .object({
//       ocr: z.array(z.record(z.any())).optional(),
//       embedding: z.array(z.record(z.any())).optional(),
//       slm: z.array(z.record(z.any())).optional(),
//       llm: z.array(z.record(z.any())).optional(),
//       reasoning: z.array(z.record(z.any())).optional(),
//       multiModal: z.array(z.record(z.any())).optional(),
//     })
//     .strict({
//       message:
//         'ai models can be ocr, embedding, llm, slm, reasoning, multimodal',
//     })
//     .refine(
//       (data) => {
//         // Ensure at least one field is present and non-empty
//         return Object.values(data).some(
//           (value) => Array.isArray(value) && value.length > 0,
//         );
//       },
//       {
//         message: 'At least one AI model type must be configured',
//       },
//     ),
// });

export const urlSchema = z.object({
  body: z.object({
    url: z.string().url(),
  }),
});

export const publicUrlSchema = z.object({
  body: z.object({
    frontendUrl: z.string().url().optional(),
    connectorUrl: z.string().url().optional(),
  }),
});

export const metricsCollectionToggleSchema = z.object({
  body: z
    .object({
      enableMetricCollection: z.boolean(),
    })
    .transform((data) => {
      return {
        enableMetricCollection: data.enableMetricCollection.toString(),
      };
    }),
});

export const metricsCollectionPushIntervalSchema = z.object({
  body: z
    .object({
      pushIntervalMs: z.number(),
    })
    .transform((data) => {
      return {
        pushIntervalMs: data.pushIntervalMs.toString(),
      };
    }),
});

export const metricsCollectionRemoteServerSchema = z.object({
  body: z.object({
    serverUrl: z.string().url(),
  }),
});


// Enum definitions
export const modelType = z.enum([
  'llm',
  'embedding', 
  'ocr',
  'slm',
  'reasoning',
  'multiModal'
]);

export const embeddingProvider = z.enum([
  'anthropic',
  'bedrock',
  'azureOpenAI', 
  'cohere',
  'default',
  'fireworks',
  'gemini',
  'huggingFace',
  'jinaAI',
  'mistral',
  'ollama',
  'openAI',
  'openAICompatible',
  'sentenceTransformers',
  'together',
  'vertexAI',
  'voyage'
]);

export const llmProvider = z.enum([
  'anthropic',
  'bedrock', 
  'azureOpenAI',
  'cohere',
  'fireworks',
  'gemini',
  'groq',
  'mistral',
  'ollama',
  'openAI',
  'openAICompatible',
  'together',
  'vertexAI',
  'xai'
]);

export const ocrProvider = z.enum([
  'azureDI',
  'ocrmypdf'
]);

// Combined provider type that accepts embedding, LLM, and OCR providers
export const providerType = z.union([embeddingProvider, llmProvider, ocrProvider]);

// Model Configuration schema
export const configurationSchema = z.object({
  model: z.string().optional().describe("Model name(s) - can be comma-separated for multiple models (e.g., 'gpt-4o, gpt-4o-mini')"),
  apiKey: z.string().optional().describe("API key for the model"),
  endpoint: z.string().optional().describe("Endpoint URL for the model"),
  organizationId: z.string().optional().describe("Organization ID"),
  deploymentName: z.string().optional().describe("Azure deployment name"),
  provider: z.string().optional().describe("Provider name"),
  awsAccessKeyId: z.string().optional().describe("AWS access key ID"),
  awsAccessSecretKey: z.string().optional().describe("AWS secret access key"),
  region: z.string().optional().describe("AWS region"),
  model_kwargs: z.record(z.any()).optional().describe("Additional model kwargs"),
  encode_kwargs: z.record(z.any()).optional().describe("Additional encoding kwargs"),
  cache_folder: z.string().optional().describe("Cache folder for models")
});

// Add Provider Request schema
export const modelConfigurationSchema = z.object({
  provider: providerType,
  configuration: configurationSchema,
  isMultimodal: z.boolean().default(false).describe("Whether the model supports multimodal input"),
  isReasoning: z.boolean().default(false).describe("Whether the model supports reasoning"),
  isDefault: z.boolean().default(false).describe("Whether this should be the default model")
});

export const updateProviderRequestSchema = z.object({
  params: z.object({
    modelType: modelType,
    modelKey: z.string().min(1, { message: 'Model key is required' }),
  }),
  body: z.object({
    provider: providerType,
    configuration: configurationSchema,
    isMultimodal: z.boolean().default(false).describe("Whether the model supports multimodal input"),
    isReasoning: z.boolean().default(false).describe("Whether the model supports reasoning"),
    isDefault: z.boolean().default(false).describe("Whether this should be the default model")
  }),
});

export const addProviderRequestSchema = z.object({
  body: z.object({
    modelType: modelType,
    provider: providerType,
    configuration: configurationSchema,
    isMultimodal: z.boolean().default(false).describe("Whether the model supports multimodal input"),
    isReasoning: z.boolean().default(false).describe("Whether the model supports reasoning"),
    isDefault: z.boolean().default(false).describe("Whether this should be the default model")
  }),
});

// Updated AI Models Config schema with proper typing
export const aiModelsConfigSchema = z.object({
  body: z
    .object({
      ocr: z.array(modelConfigurationSchema).optional(),
      embedding: z.array(modelConfigurationSchema).optional(),
      slm: z.array(modelConfigurationSchema).optional(),
      llm: z.array(modelConfigurationSchema).optional(),
      reasoning: z.array(modelConfigurationSchema).optional(),
      multiModal: z.array(modelConfigurationSchema).optional(),
    })
    .strict({
      message: 'ai models can be ocr, embedding, llm, slm, reasoning, multimodal',
    })
    .refine(
      (data) => {
        // Ensure at least one field is present and non-empty
        return Object.values(data).some(
          (value) => Array.isArray(value) && value.length > 0,
        );
      },
      {
        message: 'At least one AI model type must be configured',
      },
    ),
});



export const modelTypeSchema = z.object({
  params: z.object({
    modelType: z.enum([
      'ocr',
      'embedding',
      'llm',
      'slm',
      'reasoning',
      'multiModal',
    ]),
  }),
});

export const updateDefaultModelSchema = z.object({
  params: z.object({
    modelType: z.enum([
      'ocr',
      'embedding',
      'llm',
      'slm',
      'reasoning',
      'multiModal',
    ]),
    modelKey: z.string().min(1, { message: 'Model key is required' }),
  }),
});

export const deleteProviderSchema = z.object({
  params: z.object({
    modelType: z.enum([
      'ocr',
      'embedding',
      'llm',
      'slm',
      'reasoning',
      'multiModal',
    ]),
    modelKey: z.string().min(1, { message: 'Model key is required' }),
  }),
});
