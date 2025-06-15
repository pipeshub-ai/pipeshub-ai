import axios from 'src/utils/axios';

export interface ModelConfig {
  modelType: string;
  [key: string]: any;
}

export const getModelConfig = async (modelType: 'llm' | 'embedding' | 'ocr'): Promise<ModelConfig | null> => {
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

export const getLlmConfig = () => getModelConfig('llm');
export const updateLlmConfig = (config: ModelConfig) => updateModelConfig('llm', config);

export const getEmbeddingConfig = () => getModelConfig('embedding');
export const updateEmbeddingConfig = (config: ModelConfig) => updateModelConfig('embedding', config);
