import type {
  LlmFormValues,
  UrlFormValues,
  SmtpFormValues,
  StorageFormValues,
  EmbeddingFormValues,
} from 'src/components/dynamic-form';

import axios from 'src/utils/axios';

const API_BASE = '/api/v1/configurationManager';

// CONFIG SERVICE

export const getUniversalConfig = async (configType: string): Promise<any | null> => {
  try {
    switch (configType) {
      case 'llm':
      case 'embedding': {
        try {
          const response = await axios.get(`${API_BASE}/ai-models/${configType}`);
          const { data } = response;

          if (data.status === 'success' && data.models && data.models.length > 0) {
            const model = data.models.find((m: any) => m.isDefault) || data.models[0];
            return {
              ...model.configuration,
              providerType: model.provider,
              modelType: model.provider, // For backward compatibility
              isMultimodal: model.isMultimodal || false,
              isReasoning: model.isReasoning || false,
            };
          }

          // Special case for embedding default
          if (configType === 'embedding') {
            return {
              providerType: 'default',
              modelType: 'default',
            };
          }
        } catch (error) {
          // If no models exist, return default for embedding
          if (configType === 'embedding') {
            return {
              providerType: 'default',
              modelType: 'default',
            };
          }
          throw error;
        }
        break;
      }

      case 'storage': {
        try {
          const response = await axios.get(`${API_BASE}/storageConfig`);
          const config = response.data;

          // Map the API response to our form structure
          let providerType = 'local'; // default

          if (config.storageType === 's3') {
            providerType = 's3';
          } else if (config.storageType === 'azureBlob') {
            providerType = 'azureBlob';
          }

          return {
            providerType,
            storageType: config.storageType,
            // S3 fields
            s3AccessKeyId: config.s3AccessKeyId || '',
            s3SecretAccessKey: config.s3SecretAccessKey || '',
            s3Region: config.s3Region || '',
            s3BucketName: config.s3BucketName || '',
            // Azure fields
            accountName: config.accountName || '',
            accountKey: config.accountKey || '',
            containerName: config.containerName || '',
            endpointProtocol: config.endpointProtocol || 'https',
            endpointSuffix: config.endpointSuffix || 'core.windows.net',
            // Local fields
            mountName: config.mountName || '',
            baseUrl: config.baseUrl || '',
          };
        } catch (error) {
          // Default to local storage if no config exists
          return {
            providerType: 'local',
            storageType: 'local',
            mountName: '',
            baseUrl: '',
          };
        }
      }

      case 'url': {
        try {
          const [frontendResponse, connectorResponse] = await Promise.all([
            axios.get(`${API_BASE}/frontendPublicUrl`).catch(() => ({ data: null })),
            axios.get(`${API_BASE}/connectorPublicUrl`).catch(() => ({ data: null })),
          ]);

          return {
            providerType: 'urls',
            frontendUrl: frontendResponse.data?.url || '',
            connectorUrl: connectorResponse.data?.url || '',
          };
        } catch (error) {
          return {
            providerType: 'urls',
            frontendUrl: '',
            connectorUrl: '',
          };
        }
      }

      case 'smtp': {
        try {
          const response = await axios.get(`${API_BASE}/smtpConfig`);
          return {
            providerType: 'smtp',
            host: response.data.host || '',
            port: response.data.port || 587,
            fromEmail: response.data.fromEmail || '',
            username: response.data.username || '',
            password: response.data.password || '',
          };
        } catch (error) {
          // Default SMTP config
          return {
            providerType: 'smtp',
            host: '',
            port: 587,
            fromEmail: '',
            username: '',
            password: '',
          };
        }
      }

      default:
        break;
    }

    return null;
  } catch (error) {
    console.error(`Error fetching ${configType} configuration:`, error);
    throw error;
  }
};

export const updateUniversalConfig = async (configType: string, config: any): Promise<any> => {
  try {
    switch (configType) {
      case 'llm':
      case 'embedding': {
        // Handle embedding default case
        if (configType === 'embedding' && config.providerType === 'default') {
          // For default embedding, we don't need to do anything
          return { status: 'success' };
        }

        // Remove meta fields and prepare clean config
        const { providerType, modelType, _provider, isMultimodal, isReasoning, ...cleanConfig } = config;
        const provider = providerType || modelType;

        if (!provider) {
          throw new Error('Provider is required for AI model configuration');
        }

        // Use the individual provider API
        const requestData = {
          modelType: configType,
          provider,
          configuration: cleanConfig,
          isMultimodal: Boolean(isMultimodal),
          isReasoning: Boolean(isReasoning),
          isDefault: true,
        };

        return await axios.post(`${API_BASE}/ai-models/providers`, requestData);
      }

      case 'storage': {
        const { providerType, _provider, storageType, ...cleanConfig } = config;

        // Prepare config based on storage type
        let storageConfig: any = {
          storageType: providerType,
          ...cleanConfig,
        };

        // Handle different storage types
        switch (providerType) {
          case 's3':
            // S3 fields are all required
            storageConfig = {
              storageType: 's3',
              s3AccessKeyId: config.s3AccessKeyId,
              s3SecretAccessKey: config.s3SecretAccessKey,
              s3Region: config.s3Region,
              s3BucketName: config.s3BucketName,
            };
            break;

          case 'azureBlob':
            // Azure Blob fields
            storageConfig = {
              storageType: 'azureBlob',
              accountName: config.accountName,
              accountKey: config.accountKey,
              containerName: config.containerName,
              endpointProtocol: config.endpointProtocol || 'https',
              endpointSuffix: config.endpointSuffix || 'core.windows.net',
            };
            break;

          case 'local':
          default:
            // Local storage - handle optional fields properly
            storageConfig = {
              storageType: 'local',
            };

            // Only add optional fields if they have values
            if (config.mountName && config.mountName.trim() !== '') {
              storageConfig.mountName = config.mountName;
            }
            if (config.baseUrl && config.baseUrl.trim() !== '') {
              storageConfig.baseUrl = config.baseUrl;
            }
            break;
        }

        return await axios.post(`${API_BASE}/storageConfig`, storageConfig);
      }

      case 'url': {
        const { providerType, _provider, frontendUrl, connectorUrl, ...rest } = config;
        const normalizeUrl = (url?: string) => {
          if (!url) return '';
          const trimmed = String(url).trim();
          return (trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed)
        };
        const normalizedFrontend = normalizeUrl(frontendUrl);
        const normalizedConnector = normalizeUrl(connectorUrl);
        const apiCalls = [];

        // Only save URLs that have values
        if (normalizedFrontend) {
          apiCalls.push(axios.post(`${API_BASE}/frontendPublicUrl`, { url: normalizedFrontend }));
        }

        if (normalizedConnector) {
          apiCalls.push(axios.post(`${API_BASE}/connectorPublicUrl`, { url: normalizedConnector }));
        }

        // Execute all API calls
        if (apiCalls.length > 0) {
          return await Promise.all(apiCalls);
        }
        return await Promise.resolve();
      }

      case 'smtp': {
        const { providerType, _provider, ...cleanConfig } = config;

        // Create base config with required fields
        const smtpConfig: any = {};

        // Add required fields if they exist
        if (cleanConfig.host && cleanConfig.host.trim() !== '') {
          smtpConfig.host = cleanConfig.host.trim();
        }

        if (cleanConfig.fromEmail && cleanConfig.fromEmail.trim() !== '') {
          smtpConfig.fromEmail = cleanConfig.fromEmail.trim();
        }

        // Handle port - ensure it's a number
        if (cleanConfig.port !== undefined && cleanConfig.port !== null) {
          smtpConfig.port = Number(cleanConfig.port);
        } else {
          smtpConfig.port = 587; // Default port
        }

        // Add optional fields only if they have values
        if (cleanConfig.username && cleanConfig.username.trim() !== '') {
          smtpConfig.username = cleanConfig.username.trim();
        }

        if (cleanConfig.password && cleanConfig.password.trim() !== '') {
          smtpConfig.password = cleanConfig.password.trim();
        }

        // Validate required fields
        if (!smtpConfig.host || !smtpConfig.fromEmail) {
          throw new Error('SMTP Host and From Email are required fields');
        }

        return await axios.post(`${API_BASE}/smtpConfig`, smtpConfig);
      }

      default:
        throw new Error(`Config type ${configType} not implemented`);
    }
  } catch (error) {
    console.error(`Error updating ${configType} configuration:`, error);
    throw error;
  }
};

export const updateOnboardingAiModelsConfig = async (
  llmConfig?: { provider: string; configuration: Record<string, any>; isMultimodal?: boolean; isReasoning?: boolean }[] | null,
  embeddingConfig?: { provider: string; configuration: Record<string, any>; isMultimodal?: boolean }[] | null
): Promise<any> => {
  try {
    const promises: Promise<any>[] = [];

    // Add LLM configuration if provided
    if (llmConfig && llmConfig.length > 0) {
      const llmPromises = llmConfig.map((config) =>
        axios.post(`${API_BASE}/ai-models/providers`, {
          modelType: 'llm',
          provider: config.provider,
          configuration: config.configuration,
          isMultimodal: config.isMultimodal || false,
          isReasoning: config.isReasoning || false,
          isDefault: true,
        })
      );
      promises.push(...llmPromises);
    }

    // Add embedding configuration if provided
    if (embeddingConfig && embeddingConfig.length > 0) {
      const embeddingPromises = embeddingConfig.map((config) =>
        axios.post(`${API_BASE}/ai-models/providers`, {
          modelType: 'embedding',
          provider: config.provider,
          configuration: config.configuration,
          isMultimodal: config.isMultimodal || false,
          isDefault: true,
        })
      );
      promises.push(...embeddingPromises);
    }

    // Execute all API calls
    if (promises.length > 0) {
      return await Promise.all(promises);
    }

    return { status: 'success' };
  } catch (error) {
    console.error('Error updating onboarding AI models configuration:', error);
    throw error;
  }
};

//  SERVICE FUNCTIONS

export const getLlmConfig = () => getUniversalConfig('llm');
export const updateLlmConfig = (config: LlmFormValues) => updateUniversalConfig('llm', config);

export const getEmbeddingConfig = () => getUniversalConfig('embedding');
export const updateEmbeddingConfig = (config: EmbeddingFormValues) =>
  updateUniversalConfig('embedding', config);

export const getStorageConfig = () => getUniversalConfig('storage');
export const updateStorageConfig = (config: StorageFormValues) =>
  updateUniversalConfig('storage', config);

export const getUrlConfig = () => getUniversalConfig('url');
export const updateUrlConfig = (config: UrlFormValues) => updateUniversalConfig('url', config);

export const getSmtpConfig = () => getUniversalConfig('smtp');
export const updateSmtpConfig = (config: SmtpFormValues) => updateUniversalConfig('smtp', config);

export const updateStepperAiModelsConfig = async (
  llmConfig?: { provider: string; configuration: Record<string, any>; isMultimodal?: boolean; isReasoning?: boolean }[] | null,
  embeddingConfig?: { provider: string; configuration: Record<string, any>; isMultimodal?: boolean }[] | null
): Promise<any> => updateOnboardingAiModelsConfig(llmConfig, embeddingConfig);
