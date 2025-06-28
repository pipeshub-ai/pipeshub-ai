import {
  EmbeddingFormValues,
  LlmFormValues,
  SmtpFormValues,
  StorageFormValues,
  UrlFormValues,
} from 'src/components/dynamic-form';
import axios from 'src/utils/axios';

const API_BASE = '/api/v1/configurationManager';

// CONFIG SERVICE

export const getUniversalConfig = async (configType: string): Promise<any | null> => {
  try {
    switch (configType) {
      case 'llm':
      case 'embedding': {
        const response = await axios.get(`${API_BASE}/aiModelsConfig`);
        const { data } = response;

        if (data[configType] && data[configType].length > 0) {
          const config = data[configType][0];
          return {
            ...config.configuration,
            providerType: config.provider,
            modelType: config.provider, // For backward compatibility
          };
        }

        // Special case for embedding default
        if (configType === 'embedding') {
          return {
            providerType: 'default',
            modelType: 'default',
          };
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
        const response = await axios.get(`${API_BASE}/aiModelsConfig`);
        const currentConfig = response.data;

        // Handle embedding default case
        if (configType === 'embedding' && config.providerType === 'default') {
          const updatedConfig = {
            ...currentConfig,
            embedding: [],
          };
          return await axios.post(`${API_BASE}/aiModelsConfig`, updatedConfig);
        }

        // Remove meta fields and prepare clean config
        const { providerType, modelType, _provider, ...cleanConfig } = config;
        const provider = providerType || modelType;

        const updatedConfig = {
          ...currentConfig,
          [configType]: [
            {
              provider,
              configuration: cleanConfig,
            },
          ],
        };

        return await axios.post(`${API_BASE}/aiModelsConfig`, updatedConfig);
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
        const apiCalls = [];

        // Only save URLs that have values
        if (frontendUrl && frontendUrl.trim() !== '') {
          apiCalls.push(axios.post(`${API_BASE}/frontendPublicUrl`, { url: frontendUrl }));
        }

        if (connectorUrl && connectorUrl.trim() !== '') {
          apiCalls.push(axios.post(`${API_BASE}/connectorPublicUrl`, { url: connectorUrl }));
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
  llmConfig?: { provider: string; configuration: Record<string, any> }[] | null,
  embeddingConfig?: { provider: string; configuration: Record<string, any> }[] | null
): Promise<any> => {
  try {
    // Build the configuration payload directly (no GET needed for onboarding)
    const updatedConfig = {
      ocr: [],
      embedding: embeddingConfig || [],
      slm: [],
      llm: llmConfig || [],
      reasoning: [],
      multiModal: [],
    };

    // Send single API call with merged configuration
    return await axios.post(`${API_BASE}/aiModelsConfig`, updatedConfig);
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
  llmConfig?: { provider: string; configuration: Record<string, any> }[] | null,
  embeddingConfig?: { provider: string; configuration: Record<string, any> }[] | null
): Promise<any> => updateOnboardingAiModelsConfig(llmConfig, embeddingConfig);
