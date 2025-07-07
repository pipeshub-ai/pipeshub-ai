import axios from 'src/utils/axios';

export interface ModelConfig {
  modelType: string;
  [key: string]: any;
}

interface AiModelConfiguration {
  provider: string;
  configuration: Record<string, any>;
}

interface AiModelsConfig {
  ocr: AiModelConfiguration[];
  slm: AiModelConfiguration[];
  reasoning: AiModelConfiguration[];
  multiModal: AiModelConfiguration[];
  llm: AiModelConfiguration[];
  embedding: AiModelConfiguration[];
}

export const getModelConfig = async (
  modelType: 'llm' | 'embedding' | 'ocr'
): Promise<ModelConfig | null> => {
  try {
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const { data } = response;

    if (data[modelType] && data[modelType].length > 0) {
      const config = data[modelType][0];
      return {
        ...config.configuration,
        modelType: config.provider,
      };
    }

    if (modelType === 'embedding') {
      return { modelType: 'default' };
    }

    return null;
  } catch (error) {
    console.error(`Error fetching ${modelType} configuration:`, error);
    throw error;
  }
};

export const updateModelConfig = async (
  modelType: 'llm' | 'embedding' | 'ocr',
  config: ModelConfig
): Promise<any> => {
  try {
    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    if (modelType === 'embedding' && config.modelType === 'default') {
      const updatedConfig = {
        ...currentConfig,
        embedding: [],
      };

      const updateResponse = await axios.post(
        '/api/v1/configurationManager/aiModelsConfig',
        updatedConfig
      );
      return updateResponse;
    }

    const { modelType: configModelType, _provider, ...cleanConfig } = config;
    const provider = configModelType;

    const updatedConfig = {
      ...currentConfig,
      [modelType]: [
        {
          provider,
          configuration: cleanConfig,
        },
      ],
    };

    const updateResponse = await axios.post(
      '/api/v1/configurationManager/aiModelsConfig',
      updatedConfig
    );
    return updateResponse;
  } catch (error) {
    console.error(`Error updating ${modelType} configuration:`, error);
    throw error;
  }
};

export const updateBothModelConfigs = async (
  llmConfig?: ModelConfig,
  embeddingConfig?: ModelConfig
): Promise<any> => {
  try {
    const updatedConfig: AiModelsConfig = {
      ocr: [],
      slm: [],
      reasoning: [],
      multiModal: [],
      llm: [],
      embedding: [],
    };

    // If we're updating both configs, we can replace directly without reading current state
    if (llmConfig && embeddingConfig) {
      // Both configs provided - safe to replace directly
      if (llmConfig) {
        const { modelType: configModelType, _provider, ...cleanLlmConfig } = llmConfig;
        updatedConfig.llm = [
          {
            provider: configModelType,
            configuration: cleanLlmConfig,
          },
        ];
      }

      if (embeddingConfig.modelType === 'default') {
        updatedConfig.embedding = [];
      } else {
        const { modelType: configModelType, _provider, ...cleanEmbeddingConfig } = embeddingConfig;
        updatedConfig.embedding = [
          {
            provider: configModelType,
            configuration: cleanEmbeddingConfig,
          },
        ];
      }

      // Send update without reading current state first
      const updateResponse = await axios.post(
        '/api/v1/configurationManager/aiModelsConfig',
        updatedConfig
      );
      return updateResponse;
    }

    const response = await axios.get('/api/v1/configurationManager/aiModelsConfig');
    const currentConfig = response.data;

    updatedConfig.ocr = currentConfig.ocr || [];
    updatedConfig.slm = currentConfig.slm || [];
    updatedConfig.reasoning = currentConfig.reasoning || [];
    updatedConfig.multiModal = currentConfig.multiModal || [];

    // Update LLM config if provided, otherwise preserve existing
    if (llmConfig) {
      const { modelType: configModelType, _provider, ...cleanLlmConfig } = llmConfig;
      updatedConfig.llm = [
        {
          provider: configModelType,
          configuration: cleanLlmConfig,
        },
      ];
    } else {
      updatedConfig.llm = currentConfig.llm || [];
    }

    // Update Embedding config if provided, otherwise preserve existing
    if (embeddingConfig) {
      if (embeddingConfig.modelType === 'default') {
        updatedConfig.embedding = [];
      } else {
        const { modelType: configModelType, _provider, ...cleanEmbeddingConfig } = embeddingConfig;
        updatedConfig.embedding = [
          {
            provider: configModelType,
            configuration: cleanEmbeddingConfig,
          },
        ];
      }
    } else {
      updatedConfig.embedding = currentConfig.embedding || [];
    }

    // Send the update
    const updateResponse = await axios.post(
      '/api/v1/configurationManager/aiModelsConfig',
      updatedConfig
    );
    return updateResponse;
  } catch (error) {
    console.error('Error updating model configurations:', error);
    throw error;
  }
};

export const getLlmConfig = () => getModelConfig('llm');
export const updateLlmConfig = (config: ModelConfig) => updateModelConfig('llm', config);

export const getEmbeddingConfig = () => getModelConfig('embedding');
export const updateEmbeddingConfig = (config: ModelConfig) =>
  updateModelConfig('embedding', config);
