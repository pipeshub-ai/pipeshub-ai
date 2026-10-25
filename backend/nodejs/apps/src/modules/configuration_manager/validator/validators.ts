import { z } from 'zod';
import {
  AI_MODEL_TYPES,
  googleWorkspaceTypes,
  storageTypes,
} from '../constants/constants';
import { REASONING_EFFORT_VALUES } from '../../enterprise_search/constants/constants';
import {
  resolveS3Credentials,
  S3_PARTIAL_CREDENTIALS_MESSAGE,
} from '../../storage/utils/s3-credentials.util';
import { canSearch } from '../types/web-search.types';
import { messageWhenMissing } from '../../../libs/utils/validation.utils';
import {
  guidCheck,
  integerCheck,
  urlCheck,
} from '../../../libs/utils/zod-formats';

export const baseStorageSchema = z.object({
  storageType: z.enum([
    storageTypes.LOCAL,
    storageTypes.S3,
    storageTypes.AZURE_BLOB,
  ]),
});

const slackBotConfigBodySchema = z.object({
  name: z.string().trim().min(1, { message: 'Slack Bot name is required' }),
  botToken: z.string().trim().min(1, { message: 'Bot token is required' }),
  signingSecret: z.string().trim().min(1, { message: 'Signing secret is required' }),
  agentId: z.string().trim().optional().or(z.literal('')),
});

export const s3ConfigSchema = baseStorageSchema.extend({
  storageType: z.literal(storageTypes.S3),
  // Optional: omit both to use the EC2/ECS IAM role via the AWS default
  // credential provider chain instead of explicit IAM user credentials.
  s3AccessKeyId: z.string().optional(),
  s3SecretAccessKey: z.string().optional(),
  s3Region: z.string().min(1, { message: 'S3 region is required' }),
  s3BucketName: z.string().min(1, { message: 'S3 bucket name is required' }),
});

// The credential pair is atomic: half a pair would silently authenticate with
// the IAM role instead of the operator's IAM user.
const refineS3CredentialPair = (
  data: { s3AccessKeyId?: string; s3SecretAccessKey?: string },
  ctx: z.RefinementCtx,
): void => {
  const resolved = resolveS3Credentials({
    accessKeyId: data.s3AccessKeyId,
    secretAccessKey: data.s3SecretAccessKey,
  });
  if (resolved.kind !== 'partial') return;
  ctx.addIssue({
    code: 'custom',
    message: S3_PARTIAL_CREDENTIALS_MESSAGE,
    path: [
      resolved.missingField === 'accessKeyId'
        ? 's3AccessKeyId'
        : 's3SecretAccessKey',
    ],
  });
};

export const s3ConfigSchemaRefined =
  s3ConfigSchema.superRefine(refineS3CredentialPair);

const isS3Config = (data: {
  storageType: string;
}): data is z.infer<typeof s3ConfigSchema> =>
  data.storageType === storageTypes.S3;

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
  baseUrl: z.string().check(urlCheck()).optional(),
});

export const storageValidationSchema = z.object({
  body: z
    .discriminatedUnion('storageType', [
      s3ConfigSchema,
      azureBlobConfigSchema,
      localConfigSchema,
    ])
    .superRefine((data, ctx) => {
      if (isS3Config(data)) {
        refineS3CredentialPair(data, ctx);
      }
    }),
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
    enableJit: z.boolean().optional().default(true),
  }),
});

export const googleAuthConfigSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Google client ID is required' }),
    enableJit: z.boolean().optional().default(true),
  }),
});

export const githubAuthConfigSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'GitHub client ID is required' }),
    clientSecret: z.string().min(1, { message: 'GitHub client secret is required' }),
  }),
});

export const microsoftConfigSchema = z.object({
  clientId: z.string().min(1, { message: 'Microsoft client ID is required' }),
  tenantId: z.string().optional().default('common'),
  enableJit: z.boolean().optional().default(false),
});

export const mongoDBConfigSchema = z.object({
  body: z.object({
    uri: z.string().check(urlCheck()),
  }),
});

export const arangoDBConfigSchema = z.object({
  body: z.object({
    url: z.string().check(urlCheck()),
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
      .check(integerCheck())
      .positive()
      .max(1024 * 1024 * 1024, { message: 'Max 1GB per file' }) // safety upper bound
      .describe('Max per-file upload size in bytes'),
    featureFlags: z
      .record(z.string(), z.boolean())
      .default({})
      .describe('Feature flags map, e.g., { ENABLE_WORKFLOW_BUILDER: true }'),
  }),
});



export const createSlackBotConfigSchema = z.object({
  body: slackBotConfigBodySchema,
});

export const updateSlackBotConfigSchema = z.object({
  params: z.object({
    configId: z.string().min(1, { message: 'Config ID is required' }),
  }),
  body: slackBotConfigBodySchema,
});

export const deleteSlackBotConfigSchema = z.object({
  params: z.object({
    configId: z.string().min(1, { message: 'Config ID is required' }),
  }),
});

export const kafkaConfigSchema = z.object({
  body: z.object({
    brokers: z.array(z.string().check(urlCheck())), // Ensures an array of valid URLs
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

// zod 3 worded a missing value this way; any other wrong value keeps the stock text.
const adminConsentSchema = z.literal(
  true,
  messageWhenMissing('Has Admin Consent must be true'),
);

export const microsoftConnectorCredentialsSchema = z.object({
  body: z.object({
    clientId: z.string().min(1, { message: 'Client ID is required' }),
    clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
    tenantId: z.string().min(1, { message: 'Tenant ID is required' }),
    hasAdminConsent: adminConsentSchema,
  }),
});

export const sharepointCredentialsSchema = z.object({
  body: z
    .object({
      clientId: z.string().min(1, { message: 'Client ID is required' }),
      clientSecret: z.string().min(1, { message: 'Client Secret is required' }),
      tenantId: z.string().min(1, { message: 'Tenant ID is required' }),
      sharepointDomain: z.string().min(1, { message: 'SharePoint Domain is required' }),
      hasAdminConsent: adminConsentSchema,
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
    url: z.string().check(urlCheck()),
  }),
});

export const publicUrlSchema = z.object({
  body: z.object({
    frontendUrl: z.string().check(urlCheck()).optional(),
    connectorUrl: z.string().check(urlCheck()).optional(),
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
    serverUrl: z.string().check(urlCheck()),
  }),
});


// Enum definitions. `AI_MODEL_TYPES` is the single list; Zod wants a mutable tuple.
export const modelType = z.enum(
  AI_MODEL_TYPES as unknown as [AIModelTypeName, ...AIModelTypeName[]],
);

type AIModelTypeName = (typeof AI_MODEL_TYPES)[number];

// Provider validation is now dynamic — the Python backend registry is the
// source of truth.  We only enforce that a non-empty string is provided.
export const providerType = z.string().min(1, 'Provider is required');

// Model Configuration schema
//
// Provider-specific configuration fields (e.g. Vertex AI's `project`,
// `location`, `serviceAccountJson`; future Bedrock / Anthropic / etc. keys) are
// declared in the Python registry (`backend/python/app/config/ai_models/providers`),
// which is the source of truth — mirroring the `providerType` comment above.
// We therefore `.loose()` so any unknown keys survive validation and reach
// the Python backend; stripping them silently would otherwise produce confusing
// downstream errors like "Vertex AI requires a service account JSON" even when
// the user uploaded one.
//
export const configurationSchema = z.object({
  model: z.string().optional().describe("Model name(s) - can be comma-separated for multiple models (e.g., 'gpt-5.6-luna, gpt-5.6-terra')"),
  modelFriendlyName: z.string().optional().describe("Friendly name for the model (only allowed when model contains a single model name, not comma-separated)"),
  apiKey: z.string().optional().describe("API key for the model"),
  endpoint: z.string().optional().describe("Endpoint URL for the model"),
  organizationId: z.string().optional().describe("Organization ID"),
  deploymentName: z.string().optional().describe("Azure deployment name"),
  provider: z.string().optional().describe("Provider name"),
  awsAccessKeyId: z.string().optional().describe("AWS access key ID"),
  awsAccessSecretKey: z.string().optional().describe("AWS secret access key"),
  region: z.string().optional().describe("AWS region"),
  model_kwargs: z.record(z.string(), z.any()).optional().describe("Additional model kwargs"),
  encode_kwargs: z.record(z.string(), z.any()).optional().describe("Additional encoding kwargs"),
  cache_folder: z.string().optional().describe("Cache folder for models"),
  // Python's LLM factory treats a blank value as "no default", same as absent.
  defaultReasoningEffort: z
    .string()
    .nullable()
    .optional()
    .refine(
      (value) =>
        value == null ||
        value.trim() === '' ||
        (REASONING_EFFORT_VALUES as readonly string[]).includes(value.trim()),
      {
        message:
          'Default reasoning effort must be low, medium, high or max. Leave it empty to use the platform default.',
      },
    )
    .describe('Reasoning effort used when a request does not choose one'),
}).loose().refine(
  (data) => {
    // If modelFriendlyName is provided, model must be a single model (not comma-separated)
    if (data.modelFriendlyName && data.modelFriendlyName.trim() !== '') {
      if (!data.model || data.model.includes(',')) {
        return false;
      }
    }
    return true;
  },
  {
    message: "Model friendly name can only be set when a single model name is provided (not comma-separated)",
    path: ["modelFriendlyName"],
  }
);

// Add Provider Request schema
export const modelConfigurationSchema = z.object({
  provider: providerType,
  configuration: configurationSchema,
  isMultimodal: z.boolean().default(false).describe("Whether the model supports multimodal input"),
  isReasoning: z.boolean().default(false).describe("Whether the model supports reasoning"),
  isDefault: z.boolean().default(false).describe("Whether this should be the default model"),
  contextLength: z.number().optional().nullable().describe("Context length for the model")
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
    isDefault: z.boolean().default(false).describe("Whether this should be the default model"),
    contextLength: z.number().optional().nullable().describe("Context length for the model")
  }),
});

export const discoverModelsRequestSchema = z.object({
  body: z.object({
    provider: providerType,
    capability: z.string().optional(),
    configuration: configurationSchema.prefault({}),
    modelType: modelType.optional(),
    modelKey: z.string().optional(),
    query: z.string().optional(),
  }),
});

const batchModelSchema = z.object({
  model: z.string().min(1),
  modelFriendlyName: z.string().optional(),
  isMultimodal: z.boolean().default(false),
  isReasoning: z.boolean().default(false),
  contextLength: z.number().optional().nullable(),
});

/** Drop a shared friendly name before the single-model refine. Each batch model carries its own. */
const batchSharedConfigurationSchema = z.preprocess((value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
  if (!('modelFriendlyName' in value)) return value;
  const copy = { ...(value as Record<string, unknown>) };
  delete copy.modelFriendlyName;
  return copy;
}, configurationSchema);

export const batchAddModelsRequestSchema = z.object({
  body: z.object({
    modelType: modelType,
    provider: providerType,
    configuration: batchSharedConfigurationSchema,
    models: z.array(batchModelSchema).min(1).max(25),
    defaultModel: z.string().optional(),
    connectionId: z.string().check(guidCheck()).optional(),
  }).superRefine((body, ctx) => {
    const seen = new Set<string>();
    for (const model of body.models) {
      if (seen.has(model.model)) {
        ctx.addIssue({
          code: 'custom',
          message: `Duplicate model id ${model.model}`,
          path: ['models'],
        });
      }
      seen.add(model.model);
    }
  }),
});

export const rotateConnectionCredentialsSchema = z.object({
  body: z.object({
    configuration: z.object({
      apiKey: z.string().optional(),
      awsAccessKeyId: z.string().optional(),
      awsAccessSecretKey: z.string().optional(),
      serviceAccountJson: z.string().optional(),
    }).strict(),
  }),
  params: z.object({
    connectionId: z.string().min(1),
  }),
});

export const addProviderRequestSchema = z.object({
  body: z.object({
    modelType: modelType,
    provider: providerType,
    configuration: configurationSchema,
    isMultimodal: z.boolean().default(false).describe("Whether the model supports multimodal input"),
    isReasoning: z.boolean().default(false).describe("Whether the model supports reasoning"),
    isDefault: z.boolean().default(false).describe("Whether this should be the default model"),
    contextLength: z.number().optional().nullable().describe("Context length for the model")
  }),
});

// Updated AI Models Config schema with proper typing
export const aiModelsConfigSchema = z.object({
  body: z
    .object(
      {
        ocr: z.array(modelConfigurationSchema).optional(),
        embedding: z.array(modelConfigurationSchema).optional(),
        slm: z.array(modelConfigurationSchema).optional(),
        llm: z.array(modelConfigurationSchema).optional(),
        reasoning: z.array(modelConfigurationSchema).optional(),
        multiModal: z.array(modelConfigurationSchema).optional(),
        imageGeneration: z.array(modelConfigurationSchema).optional(),
        tts: z.array(modelConfigurationSchema).optional(),
        stt: z.array(modelConfigurationSchema).optional(),
        decision: z.array(modelConfigurationSchema).optional(),
      },
      {
        // Only the unknown-key issue carries this wording; every other issue
        // keeps zod's own message (as `.strict({ message })` did in zod 3).
        error: (issue) =>
          issue.code === 'unrecognized_keys'
            ? 'Valid properties for aiModels are ocr, embedding, llm, slm, reasoning, multiModal, imageGeneration, tts, stt, decision'
            : undefined,
      },
    )
    .strict()
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
    modelType,
  }),
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

// Web Search Provider Schemas
export const webSearchProviderType = z.enum([
  'duckduckgo',
  'serper',
  'tavily',
  'exa',
  'brave',
  'searxng',
  'firecrawl',
  'crawl4ai',
]);

const notADefaultPageReader = (body: {
  provider: string;
  isDefault?: boolean;
}): boolean => !(body.isDefault && !canSearch(body.provider));
const PAGE_READER_DEFAULT_MESSAGE = {
  message:
    'This provider only reads pages, so it cannot be the default web search provider',
  path: ['isDefault'],
};

const isServerAddress = (value: unknown): boolean => {
  if (typeof value !== 'string' || value.length > 2048) {
    return false;
  }
  try {
    const url = new URL(value);
    return (
      (url.protocol === 'http:' || url.protocol === 'https:') &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
};

export const webSearchConfigurationSchema = z
  .record(z.string(), z.any())
  .refine(
    (configuration) =>
      configuration.baseUrl === undefined ||
      isServerAddress(configuration.baseUrl),
    {
      message:
        'baseUrl must be an http(s) address of at most 2048 characters, without a user name or password',
      path: ['baseUrl'],
    },
  )
  .describe(
    'Provider-specific configuration (apiKey; baseUrl for searxng, firecrawl and crawl4ai)',
  );

export const webSearchProviderConfigSchema = z.object({
  provider: webSearchProviderType,
  configuration: webSearchConfigurationSchema,
  isDefault: z
    .boolean()
    .default(false)
    .describe('Whether this should be the default provider'),
});

export const addWebSearchProviderSchema = z.object({
  body: z
    .object({
      provider: webSearchProviderType,
      configuration: webSearchConfigurationSchema,
      isDefault: z
        .boolean()
        .default(false)
        .describe('Whether this should be the default provider'),
    })
    .refine(notADefaultPageReader, PAGE_READER_DEFAULT_MESSAGE),
});

export const updateWebSearchProviderSchema = z.object({
  params: z.object({
    providerKey: z.string().min(1, { message: 'Provider key is required' }),
  }),
  body: z
    .object({
      provider: webSearchProviderType,
      configuration: webSearchConfigurationSchema,
      isDefault: z
        .boolean()
        .default(false)
        .describe('Whether this should be the default provider'),
    })
    .refine(notADefaultPageReader, PAGE_READER_DEFAULT_MESSAGE),
});

export const deleteWebSearchProviderSchema = z.object({
  params: z.object({
    providerKey: z.string().min(1, { message: 'Provider key is required' }),
  }),
});

export const updateDefaultWebSearchProviderSchema = z.object({
  params: z.object({
    providerKey: z.string().min(1, { message: 'Provider key is required' }),
  }),
});

export const webSearchSettingsSchema = z
  .object({
    includeImages: z.boolean(),
    maxImages: z.number().check(integerCheck()).min(1).max(500).optional(),
  })
  .superRefine((settings, ctx) => {
    if (settings.includeImages && settings.maxImages === undefined) {
      ctx.addIssue({
        code: 'custom',
        path: ['maxImages'],
        message: 'maxImages is required when includeImages is true',
      });
    }
  });

export const updateWebSearchSettingsSchema = z.object({
  body: webSearchSettingsSchema,
});

const identityHeaderName = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9!#$%&'*+.^_`|~-]+$/, {
    message: 'Not a valid header name',
  });

export const identityPolicyBodySchema = z
  .object({
    subjectSource: z.enum(['userId', 'email', 'idpObjectId']).default('userId'),
    includeEmail: z.boolean().default(false),
    includeDisplayName: z.boolean().default(false),
    groups: z
      .object({
        enabled: z.boolean().default(true),
        allowList: z
          .array(z.string().trim().min(1).max(256))
          .max(500)
          .default([]),
        maxForwarded: z
          .number()
          .check(integerCheck())
          .min(1)
          .max(500)
          .default(50),
      })
      .strict()
      .prefault({}),
    pseudonymize: z.boolean().default(false),
    onMissingIdentity: z.enum(['service', 'block']).default('service'),
    queryEmbeddingIdentity: z.enum(['user', 'service']).default('user'),
    serviceIdentityPrefix: z
      .string()
      .trim()
      .min(1)
      .max(64)
      .default('svc:pipeshub'),
  })
  .strict();

export const identityPolicySchema = z.object({
  body: identityPolicyBodySchema,
});

export const connectionIdentityForwardingSchema = z.object({
  params: z.object({ connectionId: z.string().min(1) }),
  body: z
    .object({
      enabled: z.boolean(),
      mode: z.enum(['headers', 'template', 'signedJwt']).default('headers'),
      preset: z
        .enum([
          'litellm',
          'portkey',
          'helicone',
          'cloudflare',
          'apim',
          'custom',
        ])
        .default('litellm'),
      headerTemplates: z
        .record(identityHeaderName, z.string().max(512))
        .refine((templates) => Object.keys(templates).length <= 16, {
          message: 'At most 16 header templates',
        })
        .default({}),
      jwtAudience: z.string().trim().max(256).optional(),
      serviceAuth: z.enum(['apiKey', 'jwt']).default('apiKey'),
      // Absent keeps the stored key; an empty string clears it.
      serviceApiKey: z.string().max(512).optional(),
    })
    .strict(),
});

export const identityPreviewSchema = z.object({
  body: z
    .object({
      userId: z.string().min(1).optional(),
      workload: z.string().min(1).max(64).default('chat'),
      configuration: z.record(z.string(), z.any()).default({}),
      connectionId: z.string().min(1).optional(),
    })
    .strict(),
});
