import { z } from 'zod';
import { storageTypes } from '../constants/constants';

// ============================================================================
// Shared Constants
// ============================================================================

const AI_MODEL_TYPE_VALUES = [
  'llm',
  'embedding',
  'ocr',
  'slm',
  'reasoning',
  'multiModal',
] as const;

const EMBEDDING_PROVIDER_VALUES = [
  'anthropic',
  'bedrock',
  'azureAI',
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
  'voyage',
] as const;

const LLM_PROVIDER_VALUES = [
  'anthropic',
  'bedrock',
  'azureAI',
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
  'xai',
] as const;

const OCR_PROVIDER_VALUES = ['azureDI', 'ocrmypdf'] as const;

// ============================================================================
// Request Schemas - Storage
// ============================================================================

export const baseStorageSchema = z.object({
  storageType: z.enum([storageTypes.LOCAL, storageTypes.S3, storageTypes.AZURE_BLOB]),
});

export const s3ConfigSchema = baseStorageSchema
  .extend({
    storageType: z.literal(storageTypes.S3),
    s3AccessKeyId: z.string().min(1, { message: 'S3 access key ID is required' }),
    s3SecretAccessKey: z.string().min(1, { message: 'S3 secret access key is required' }),
    s3Region: z.string().min(1, { message: 'S3 region is required' }),
    s3BucketName: z.string().min(1, { message: 'S3 bucket name is required' }),
  })
  .strict();

export const azureBlobConfigSchema = baseStorageSchema
  .extend({
    storageType: z.literal(storageTypes.AZURE_BLOB),
    endpointProtocol: z.enum(['http', 'https']).optional().default('https'),
    accountName: z.string().min(1, { message: 'Azure account name is required' }).optional(),
    accountKey: z.string().min(1, { message: 'Azure account key is required' }).optional(),
    endpointSuffix: z
      .string()
      .min(1, { message: 'Azure endpoint suffix is required' })
      .optional()
      .default('core.windows.net'),
    containerName: z.string().min(1, { message: 'Azure container name is required' }),
  })
  .strict();

export const localConfigSchema = baseStorageSchema
  .extend({
    storageType: z.literal(storageTypes.LOCAL),
    mountName: z.string().optional(),
    baseUrl: z.string().url().optional(),
  })
  .strict();

const storageConfigBodySchema = z
  .discriminatedUnion('storageType', [s3ConfigSchema, azureBlobConfigSchema, localConfigSchema])

export const storageValidationSchema = z.object({ body: storageConfigBodySchema });

// ============================================================================
// Request Schemas - SMTP
// ============================================================================

export const smtpConfigSchema = z.object({
  body: z
    .object({
      host: z.string().min(1, { message: 'SMTP host is required' }),
      port: z.coerce.number().int().min(1, { message: 'SMTP port is required' }),
      username: z.string().optional(),
      password: z.string().optional(),
      fromEmail: z.string().min(1, { message: 'SMTP from is required' }),
    })
    .strict(),
});

// ============================================================================
// Request Schemas - Auth
// ============================================================================

export const azureAdConfigSchema = z.object({
  body: z
    .object({
      clientId: z.string().trim().min(1, { message: 'Azure client ID is required' }),
      tenantId: z.string().trim().optional().default('common'),
      enableJit: z.boolean().optional().default(true),
    })
    .strict(),
});

export const microsoftAuthConfigSchema = z.object({
  body: z
    .object({
      clientId: z.string().trim().min(1, { message: 'Microsoft client ID is required' }),
      tenantId: z.string().trim().optional().default('common'),
      enableJit: z.boolean().optional().default(true),
    })
    .strict(),
});

export const ssoConfigSchema = z.object({
  body: z
    .object({
      entryPoint: z.string().trim().min(1, { message: 'SSO entry point is required' }),
      certificate: z.string().trim().min(1, { message: 'SSO certificate is required' }),
      emailKey: z.string().trim().min(1, { message: 'SSO Email Key is required' }),
      enableJit: z.boolean().optional().default(true),
      samlPlatform: z.string().optional(),
    })
    .passthrough(),
});

export const googleAuthConfigSchema = z.object({
  body: z
    .object({
      clientId: z.string().trim().min(1, { message: 'Google client ID is required' }),
      enableJit: z.boolean().optional().default(true),
    })
    .strict(),
});

export const oauthConfigSchema = z.object({
  body: z
    .object({
      providerName: z.string().trim().min(1, { message: 'Provider name is required' }),
      clientId: z.string().trim().min(1, { message: 'Client ID is required' }),
      clientSecret: z.string().trim().optional(),
      authorizationUrl: z.string().trim().url().optional().or(z.literal('')),
      tokenEndpoint: z.string().trim().url().optional().or(z.literal('')),
      userInfoEndpoint: z.string().trim().url().optional().or(z.literal('')),
      scope: z.string().optional(),
      redirectUri: z.string().trim().url().optional().or(z.literal('')),
      enableJit: z.boolean().optional().default(true),
    })
    .strict(),
});

// Backward compatible alias
export const microsoftConfigSchema = microsoftAuthConfigSchema;

// ============================================================================
// Request Schemas - Data/Infra (Legacy — not exposed via cm_routes)
// ============================================================================

export const mongoDBConfigSchema = z.object({
  body: z.object({ uri: z.string().url() }).strict(),
});

export const arangoDBConfigSchema = z.object({
  body: z
    .object({
      url: z.string().url(),
      username: z.string().optional(),
      password: z.string().optional(),
    })
    .strict(),
});

export const dbConfigSchema = z.object({
  body: z
    .object({
      mongodb: mongoDBConfigSchema.optional(),
      arangodb: arangoDBConfigSchema.optional(),
    })
    .refine(
      (data) => [data.mongodb, data.arangodb].filter(Boolean).length >= 1,
      {
        message:
          "At least one database configuration must be provided: 'mongodb' and/or 'arangodb'",
        path: ['body'],
      },
    ),
});

export const redisConfigSchema = z.object({
  body: z
    .object({
      host: z.string().min(1, { message: 'Redis host is required' }),
      port: z.number().min(1, { message: 'Redis port is required' }),
      password: z.string().optional(),
      tls: z.boolean().optional(),
    })
    .strict(),
});

export const qdrantConfigSchema = z.object({
  body: z
    .object({
      host: z.string().min(1, { message: 'Qdrant host is required' }),
      port: z.number().min(1, { message: 'Qdrant Port is required' }),
      grpcPort: z.number().optional(),
      apiKey: z.string().optional(),
    })
    .strict(),
});

export const kafkaConfigSchema = z.object({
  body: z
    .object({
      brokers: z.array(z.string().url()),
      sasl: z
        .object({ mechanism: z.string(), username: z.string(), password: z.string() })
        .strict()
        .optional(),
    })
    .strict(),
});

export const urlSchema = z.object({
  body: z.object({ url: z.string().trim().url() }).strict(),
});

export const publicUrlSchema = z.object({
  body: z
    .object({
      frontendUrl: z.string().trim().url().optional(),
      connectorUrl: z.string().trim().url().optional(),
    })
    .strict(),
});

// ============================================================================
// Request Schemas - Platform Settings
// ============================================================================

export const platformSettingsSchema = z.object({
  body: z
    .object({
      fileUploadMaxSizeBytes: z.coerce
        .number()
        .int()
        .positive()
        .max(1024 * 1024 * 1024, { message: 'Max 1GB per file' })
        .describe('Max per-file upload size in bytes'),
      featureFlags: z
        .record(z.boolean())
        .default({})
        .describe('Feature flags map, e.g., { ENABLE_WORKFLOW_BUILDER: true }'),
    })
    .strict(),
});

// ============================================================================
// Request Schemas - Slack Bot
// ============================================================================

const slackBotConfigBodySchema = z
  .object({
    name: z.string().trim().min(1, { message: 'Slack Bot name is required' }),
    botToken: z.string().trim().min(1, { message: 'Bot token is required' }),
    signingSecret: z.string().trim().min(1, { message: 'Signing secret is required' }),
    agentId: z.string().trim().optional().or(z.literal('')),
  })
  .strict();

export const slackBotConfigIdParamsSchema = z.object({
  configId: z.string().min(1, { message: 'Config ID is required' }),
});

export const createSlackBotConfigSchema = z.object({ body: slackBotConfigBodySchema });

export const updateSlackBotConfigSchema = z.object({
  params: slackBotConfigIdParamsSchema,
  body: slackBotConfigBodySchema,
});

export const deleteSlackBotConfigSchema = z.object({ params: slackBotConfigIdParamsSchema });

// ============================================================================
// Request Schemas - Custom System Prompt
// ============================================================================

export const customSystemPromptSchema = z.object({
  body: z.object({ customSystemPrompt: z.string() }).strict(),
});

// ============================================================================
// Request Schemas - Metrics Collection
// ============================================================================

export const metricsCollectionToggleSchema = z.object({
  body: z.object({ enableMetricCollection: z.boolean() }).strict(),
});

export const metricsCollectionPushIntervalSchema = z.object({
  body: z.object({ pushIntervalMs: z.coerce.number().int().positive() }).strict(),
});

export const metricsCollectionRemoteServerSchema = z.object({
  body: z.object({ serverUrl: z.string().trim().url() }).strict(),
});

// ============================================================================
// Request Schemas - AI Models
// ============================================================================

export const modelType = z.enum(AI_MODEL_TYPE_VALUES);
export const embeddingProvider = z.enum(EMBEDDING_PROVIDER_VALUES);
export const llmProvider = z.enum(LLM_PROVIDER_VALUES);
export const ocrProvider = z.enum(OCR_PROVIDER_VALUES);

/** Combined provider enum for embedding, LLM, and OCR providers. */
export const providerType = z.union([embeddingProvider, llmProvider, ocrProvider]);

/**
 * NOT strict — AI provider configuration keys vary by provider.
 * Unknown keys are stripped (default z.object behaviour) rather than rejected.
 */
export const configurationSchema = z
  .object({
    model: z
      .string()
      .optional()
      .describe("Model name(s) — comma-separated for multiple (e.g. 'gpt-4o, gpt-4o-mini')"),
    modelFriendlyName: z
      .string()
      .optional()
      .describe('Friendly name (only allowed when model is a single name, not comma-separated)'),
    apiKey: z.string().optional(),
    endpoint: z.string().optional(),
    organizationId: z.string().optional(),
    deploymentName: z.string().optional(),
    provider: z.string().optional(),
    awsAccessKeyId: z.string().optional(),
    awsAccessSecretKey: z.string().optional(),
    region: z.string().optional(),
    model_kwargs: z.record(z.any()).optional(),
    encode_kwargs: z.record(z.any()).optional(),
    cache_folder: z.string().optional(),
  })
  .refine(
    (data) => {
      if (data.modelFriendlyName && data.modelFriendlyName.trim() !== '') {
        if (!data.model || data.model.includes(',')) return false;
      }
      return true;
    },
    {
      message:
        'Model friendly name can only be set when a single model name is provided (not comma-separated)',
      path: ['modelFriendlyName'],
    },
  );

export const modelConfigurationSchema = z.object({
  provider: providerType,
  configuration: configurationSchema,
  isMultimodal: z.boolean().default(false).describe('Whether the model supports multimodal input'),
  isReasoning: z.boolean().default(false).describe('Whether the model supports reasoning'),
  isDefault: z.boolean().default(false).describe('Whether this should be the default model'),
  contextLength: z.number().optional().nullable().describe('Context length for the model'),
});

export const addProviderRequestSchema = z.object({
  body: z
    .object({
      modelType: modelType,
      provider: providerType,
      configuration: configurationSchema,
      isMultimodal: z.boolean().default(false),
      isReasoning: z.boolean().default(false),
      isDefault: z.boolean().default(false),
      contextLength: z.number().optional().nullable(),
    })
    .strict(),
});

export const updateProviderRequestSchema = z.object({
  params: z.object({
    modelType: modelType,
    modelKey: z.string().min(1, { message: 'Model key is required' }),
  }),
  body: z
    .object({
      provider: providerType,
      configuration: configurationSchema,
      isMultimodal: z.boolean().default(false),
      isReasoning: z.boolean().default(false),
      isDefault: z.boolean().default(false),
      contextLength: z.number().optional().nullable(),
    })
    .strict(),
});

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
      message:
        'Valid properties for aiModels are ocr, embedding, llm, slm, reasoning, multiModal',
    })
    .refine(
      (data) => Object.values(data).some((value) => Array.isArray(value) && value.length > 0),
      { message: 'At least one AI model type must be configured' },
    ),
});

export const modelTypeSchema = z.object({
  params: z.object({ modelType }),
});

export const updateDefaultModelSchema = z.object({
  params: z.object({
    modelType,
    modelKey: z.string().min(1, { message: 'Model key is required' }),
  }),
});

export const deleteProviderSchema = z.object({
  params: z.object({
    modelType,
    modelKey: z.string().min(1, { message: 'Model key is required' }),
  }),
});

// ============================================================================
// Response Schemas - Shared primitives
// ============================================================================

/**
 * Accepts both native numbers and numeric strings (e.g. from Redis/KV stores
 * that stringify numbers on retrieval).
 */
const numericStringSchema = z
  .string()
  .regex(/^-?\d+(\.\d+)?$/, { message: 'Expected numeric string' });
const booleanLikeSchema = z.union([z.boolean(), z.literal('true'), z.literal('false')]);
const numberLikeSchema = z.union([z.number(), numericStringSchema]);

export const cmMessageResponseSchema = z
  .object({
    message: z.string(),
    warningMessage: z.unknown().optional(),
  })
  .strict();

export const cmSuccessStatusSchema = z
  .object({
    status: z.literal('success'),
  })
  .strict();

// ============================================================================
// Response Schemas - Storage
// ============================================================================

/**
 * Strict discriminated union — every branch requires `storageType` and only
 * exposes known fields. Extra fields from the KV store are caught by safeParse.
 */
export const storageConfigResponseSchema = z.discriminatedUnion('storageType', [
  z
    .object({
      storageType: z.literal(storageTypes.S3),
      accessKeyId: z.string(),
      secretAccessKey: z.string(),
      region: z.string(),
      bucketName: z.string(),
    })
    .strict(),
  z
    .object({
      storageType: z.literal(storageTypes.AZURE_BLOB),
      endpointProtocol: z.string().optional(),
      accountName: z.string().optional(),
      accountKey: z.string().optional(),
      endpointSuffix: z.string().optional(),
      containerName: z.string().optional(),
      // Connection strings are not echoed back for security reasons
    })
    .strict(),
  z
    .object({
      storageType: z.literal(storageTypes.LOCAL),
      mountName: z.string().optional(),
      baseUrl: z.string().optional(),
    })
    .strict(),
]);

// ============================================================================
// Response Schemas - SMTP
// ============================================================================

/** All fields optional — returns {} when SMTP is not yet configured. */
export const smtpConfigResponseSchema = z
  .object({
    host: z.string().optional(),
    port: numberLikeSchema.optional(),
    username: z.string().optional(),
    password: z.string().optional(),
    fromEmail: z.string().optional(),
  })
  .strict();

// ============================================================================
// Response Schemas - Auth
// ============================================================================

/** All fields optional — returns {} when the auth provider is not configured. */
export const azureAdAuthConfigResponseSchema = z
  .object({
    clientId: z.string().optional(),
    tenantId: z.string().optional(),
    authority: z.string().optional(),
    enableJit: z.boolean().optional(),
  })
  .strict();

export const microsoftAuthConfigResponseSchema = azureAdAuthConfigResponseSchema;

export const googleAuthConfigResponseSchema = z
  .object({
    clientId: z.string().optional(),
    enableJit: z.boolean().optional(),
  })
  .strict();

export const ssoAuthConfigResponseSchema = z
  .object({
    certificate: z.string().optional(),
    entryPoint: z.string().optional(),
    emailKey: z.string().optional(),
    enableJit: z.boolean().optional(),
    samlPlatform: z.string().optional(),
  })
  .strict();

export const oauthAuthConfigResponseSchema = z
  .object({
    providerName: z.string().optional(),
    clientId: z.string().optional(),
    clientSecret: z.string().optional(),
    authorizationUrl: z.string().optional(),
    tokenEndpoint: z.string().optional(),
    userInfoEndpoint: z.string().optional(),
    scope: z.string().optional(),
    redirectUri: z.string().optional().or(z.literal('')),
    enableJit: z.boolean().optional(),
  })
  .strict();

// ============================================================================
// Response Schemas - Platform
// ============================================================================

export const platformSettingsResponseSchema = z
  .object({
    fileUploadMaxSizeBytes: z.number(),
    featureFlags: z.record(z.boolean()),
  })
  .strict();

export const availablePlatformFlagsResponseSchema = z
  .object({
    flags: z.array(
      z
        .object({
          key: z.string(),
          label: z.string(),
          description: z.string().optional(),
          defaultEnabled: z.boolean().optional(),
        })
        .strict(),
    ),
  })
  .strict();

// ============================================================================
// Response Schemas - Slack Bot
// ============================================================================

export const slackBotResponseConfigSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    agentId: z.string().nullable(),
    createdAt: z.string(),
    updatedAt: z.string(),
    botToken: z.string(),
    signingSecret: z.string(),
  })
  .strict();

export const slackBotConfigsResponseSchema = cmSuccessStatusSchema
  .extend({
    configs: z.array(slackBotResponseConfigSchema),
  })
  .strict();

export const slackBotConfigMutationResponseSchema = cmSuccessStatusSchema
  .extend({
    config: slackBotResponseConfigSchema,
  })
  .strict();

export const slackBotConfigDeleteResponseSchema = cmSuccessStatusSchema
  .extend({
    message: z.string(),
  })
  .strict();

// ============================================================================
// Response Schemas - Custom System Prompt
// ============================================================================

export const customSystemPromptResponseSchema = z
  .object({
    customSystemPrompt: z.string(),
  })
  .strict();

export const customSystemPromptUpdateResponseSchema = cmMessageResponseSchema
  .extend({
    customSystemPrompt: z.string(),
  })
  .strict();

// ============================================================================
// Response Schemas - URL (Legacy)
// ============================================================================

export const frontendPublicUrlResponseSchema = z.union([
  z.object({ url: z.string().url() }).strict(),
  z.object({}).strict(),
]);

export const connectorPublicUrlResponseSchema = frontendPublicUrlResponseSchema;



// ============================================================================
// Response Schemas - Database
// ============================================================================

export const arangoDbConfigResponseSchema = z.object({
  url: z.string().url(),
  username: z.string(),
  password: z.string(),
  db: z.string(),
});

export const mongoDbConfigResponseSchema = z.object({
  uri: z.string().url(),
  db: z.string(),
});

export const redisConfigResponseSchema = z.object({
  host: z.string(),
  port: z.number(),
  password: z.string(),
  tls: z.boolean(),
});

export const kafkaConfigResponseSchema = z.object({
  brokers: z.array(z.string()),
  sasl: z.object({
    username: z.string(),
    password: z.string(),
  }),
});

export const qdrantConfigResponseSchema = z.object({
  port: z.number(),
  apiKey: z.string(),
  host: z.string(),
  grpcPort: z.number(),
});

// ============================================================================
// Response Schemas - AI Models
// ============================================================================

/**
 * Individual model config item.
 * NOT strict — AI provider configs contain provider-specific fields not
 * enumerated at this level. passthrough() preserves them for callers.
 */
export const aiModelConfigItemSchema = z
  .object({
    provider: z.string().optional(),
    configuration: z.record(z.any()).optional(),
    modelKey: z.string().optional(),
    isMultimodal: z.boolean().optional(),
    isReasoning: z.boolean().optional(),
    isDefault: z.boolean().optional(),
    contextLength: z.number().nullable().optional(),
    modelFriendlyName: z.string().optional(),
  });

export const aiModelsConfigResponseSchema = z
  .object({
    ocr: z.array(aiModelConfigItemSchema).optional(),
    embedding: z.array(aiModelConfigItemSchema).optional(),
    slm: z.array(aiModelConfigItemSchema).optional(),
    llm: z.array(aiModelConfigItemSchema).optional(),
    reasoning: z.array(aiModelConfigItemSchema).optional(),
    multiModal: z.array(aiModelConfigItemSchema).optional(),
    customSystemPrompt: z.string().optional(),
  });

export const aiModelsByTypeResponseSchema = cmSuccessStatusSchema
  .extend({
    models: z.array(aiModelConfigItemSchema),
    message: z.string(),
  })
  .strict();

export const aiModelsAvailableItemResponseSchema = z
  .object({
    modelType: modelType.optional(),
    provider: z.string(),
    modelName: z.string(),
    modelKey: z.string(),
    isMultimodal: z.boolean().optional(),
    isReasoning: z.boolean().optional(),
    isDefault: z.boolean().optional(),
    modelFriendlyName: z.string().optional(),
  })
  .strict();

export const aiModelsAvailableByTypeResponseSchema = cmSuccessStatusSchema
  .extend({
    models: z.array(aiModelsAvailableItemResponseSchema),
    message: z.string(),
  })
  .strict();

export const aiModelsProvidersResponseSchema = cmSuccessStatusSchema
  .extend({
    models: z
      .object({
        ocr: z.array(aiModelConfigItemSchema),
        embedding: z.array(aiModelConfigItemSchema),
        slm: z.array(aiModelConfigItemSchema),
        llm: z.array(aiModelConfigItemSchema),
        reasoning: z.array(aiModelConfigItemSchema),
        multiModal: z.array(aiModelConfigItemSchema),
        customSystemPrompt: z.string(),
      })
      .strict(),
    message: z.string(),
  })
  .strict();

export const aiModelMutationResponseSchema = cmSuccessStatusSchema
  .extend({
    message: z.string(),
    details: z
      .object({
        modelKey: z.string(),
        modelType,
        provider: z.string().optional(),
        model: z.string().optional(),
        isDefault: z.boolean().optional(),
        wasDefault: z.boolean().optional(),
        contextLength: z.number().nullable().optional(),
        isMultimodal: z.boolean().optional(),
        isReasoning: z.boolean().optional(),
      })
      .strict(),
  })
  .strict();

// ============================================================================
// Response Schemas - Metrics Collection
// ============================================================================

export const metricsCollectionResponseSchema = z
  .object({
    enableMetricCollection: booleanLikeSchema.optional(),
    pushIntervalMs: numberLikeSchema.optional(),
    serverUrl: z.string().optional(),
  })

// ============================================================================
// Inferred Request Types
// (Import these in controllers instead of typing req.body as `any`)
// ============================================================================

// — Body types —
export type StorageConfigBody = z.infer<typeof storageValidationSchema>['body'];
export type SmtpConfigBody = z.infer<typeof smtpConfigSchema>['body'];
export type AzureAdConfigBody = z.infer<typeof azureAdConfigSchema>['body'];
export type MicrosoftAuthConfigBody = z.infer<typeof microsoftAuthConfigSchema>['body'];
export type SsoConfigBody = z.infer<typeof ssoConfigSchema>['body'];
export type GoogleAuthConfigBody = z.infer<typeof googleAuthConfigSchema>['body'];
export type OAuthConfigBody = z.infer<typeof oauthConfigSchema>['body'];
export type PlatformSettingsBody = z.infer<typeof platformSettingsSchema>['body'];
export type CreateSlackBotConfigBody = z.infer<typeof createSlackBotConfigSchema>['body'];
export type UpdateSlackBotConfigBody = z.infer<typeof updateSlackBotConfigSchema>['body'];
export type CustomSystemPromptBody = z.infer<typeof customSystemPromptSchema>['body'];
export type AIModelsConfigBody = z.infer<typeof aiModelsConfigSchema>['body'];
export type AddProviderBody = z.infer<typeof addProviderRequestSchema>['body'];
export type UpdateProviderBody = z.infer<typeof updateProviderRequestSchema>['body'];
export type MetricsCollectionToggleBody = z.infer<typeof metricsCollectionToggleSchema>['body'];
export type MetricsCollectionPushIntervalBody = z.infer<
  typeof metricsCollectionPushIntervalSchema
>['body'];
export type MetricsCollectionRemoteServerBody = z.infer<
  typeof metricsCollectionRemoteServerSchema
>['body'];

// — Param types —
export type UpdateSlackBotConfigParams = z.infer<typeof updateSlackBotConfigSchema>['params'];
export type DeleteSlackBotConfigParams = z.infer<typeof deleteSlackBotConfigSchema>['params'];
export type UpdateProviderParams = z.infer<typeof updateProviderRequestSchema>['params'];
export type DeleteProviderParams = z.infer<typeof deleteProviderSchema>['params'];
export type ModelTypeParams = z.infer<typeof modelTypeSchema>['params'];
export type UpdateDefaultModelParams = z.infer<typeof updateDefaultModelSchema>['params'];

// — Response types —
export type StorageConfigResponse = z.infer<typeof storageConfigResponseSchema>;
export type SmtpConfigResponse = z.infer<typeof smtpConfigResponseSchema>;
export type PlatformSettingsResponse = z.infer<typeof platformSettingsResponseSchema>;
export type SlackBotConfigEntry = z.infer<typeof slackBotResponseConfigSchema>;
export type SlackBotConfigsResponse = z.infer<typeof slackBotConfigsResponseSchema>;
export type AIModelsConfigResponse = z.infer<typeof aiModelsConfigResponseSchema>;
export type AIModelMutationResponse = z.infer<typeof aiModelMutationResponseSchema>;
export type MetricsCollectionResponse = z.infer<typeof metricsCollectionResponseSchema>;
