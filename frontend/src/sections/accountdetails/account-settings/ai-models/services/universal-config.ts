import axios from 'src/utils/axios';
import { ModelType, ConfiguredModel, ModelData } from '../types';

export const modelService = {
  // Get all models for a type
  async getAllModels(modelType: ModelType): Promise<ConfiguredModel[]> {
    try {
      const response = await axios.get(`/api/v1/configurationManager/ai-models/${modelType}`);
      if (response.data.status === 'success' && response.data.models) {
        return response.data.models.map((model: any) => ({
          id: model.modelKey || model.id || `${model.provider}-${Date.now()}`,
          modelKey: model.modelKey,
          name: model.name || `${model.provider} ${modelType.toUpperCase()} Model`,
          provider: model.provider,
          modelType,
          configuration: model.configuration || {},
          isActive: model.isActive || false,
          isDefault: model.isDefault || false,
          isMultimodal: model.isMultimodal || false,
          isReasoning: model.isReasoning || false,
        }));
      }
      return [];
    } catch (err) {
      console.error(`Error fetching ${modelType} models:`, err);
      return [];
    }
  },

  // Add new model
  async addModel(modelType: ModelType, modelData: ModelData): Promise<any> {
    try {
      const requestData = {
        modelType,
        provider: modelData.provider,
        configuration: modelData.configuration,
        isMultimodal: modelData.isMultimodal || false,
        isReasoning: modelData.isReasoning || false,
        isDefault: modelData.isDefault || false,
      };

      const response = await axios.post(
        '/api/v1/configurationManager/ai-models/providers',
        requestData
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to add model');
    } catch (err: any) {
      console.error(`Error adding ${modelType} model:`, err);
      throw new Error(
        err.response?.data?.message || err.message || `Failed to add ${modelType} model`
      );
    }
  },

  // Update model
  async updateModel(modelType: ModelType, modelKey: string, modelData: ModelData): Promise<any> {
    try {
      const requestData = {
        provider: modelData.provider,
        configuration: modelData.configuration,
        isMultimodal: modelData.isMultimodal || false,
        isDefault: modelData.isDefault || false,
        isReasoning: modelData.isReasoning || false,
      };

      const response = await axios.put(
        `/api/v1/configurationManager/ai-models/providers/${modelType}/${modelKey}`,
        requestData
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to update model');
    } catch (err: any) {
      console.error(`Error updating model:`, err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to update model');
    }
  },

  // Delete model
  async deleteModel(modelType: ModelType, modelKey: string): Promise<any> {
    try {
      const response = await axios.delete(
        `/api/v1/configurationManager/ai-models/providers/${modelType}/${modelKey}`
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to delete model');
    } catch (err: any) {
      console.error(`Error deleting model:`, err);
      throw new Error(err.response?.data?.message || err.message || 'Failed to delete model');
    }
  },

  // Set default model
  async setDefaultModel(modelType: ModelType, modelKey: string): Promise<any> {
    try {
      const response = await axios.put(
        `/api/v1/configurationManager/ai-models/default/${modelType}/${modelKey}`
      );

      if (response.data.status === 'success') {
        return response.data;
      }
      throw new Error(response.data.message || 'Failed to set default model');
    } catch (err: any) {
      console.error(`Error setting default model:`, err);
      throw new Error(
        err.response?.data?.message || err.message || 'Failed to set default model'
      );
    }
  },

  // Get/Update legacy config (for dynamic form compatibility)
  async getLlmConfig(): Promise<any> {
    const models = await this.getAllModels('llm');
    const activeModel = models.find((m) => m.isDefault);
    if (activeModel) {
      return {
        ...activeModel.configuration,
        providerType: activeModel.provider,
        modelType: activeModel.provider,
        isMultimodal: activeModel.isMultimodal,
        isReasoning: activeModel.isReasoning,
      };
    }
    return null;
  },

  async getEmbeddingConfig(): Promise<any> {
    const models = await this.getAllModels('embedding');
    const activeModel = models.find((m) => m.isDefault);
    if (activeModel) {
      return {
        ...activeModel.configuration,
        providerType: activeModel.provider,
        modelType: activeModel.provider,
        isMultimodal: activeModel.isMultimodal,
      };
    }
    // Default embedding fallback
    return {
      providerType: 'default',
      modelType: 'default',
    };
  },

  async updateLlmConfig(config: any): Promise<any> {
    const { modelType, providerType, _provider, isMultimodal, isReasoning, ...cleanConfig } = config;
    console.log("isMultimodal", isMultimodal);
    console.log("cleanConfig", cleanConfig);
    const provider = providerType || modelType || _provider;

    // Update or create model
    const models = await this.getAllModels('llm');
    const existingModel = models.find((m) => m.provider === provider && m.isDefault);

    if (existingModel) {
      return this.updateModel('llm', existingModel.modelKey || existingModel.id, {
        provider,
        configuration: cleanConfig,
        isDefault: true,
        isMultimodal: Boolean(isMultimodal),
        isReasoning: Boolean(isReasoning),
      });
    }
    return this.addModel('llm', {
      provider,
      configuration: cleanConfig,
      isDefault: true,
      isMultimodal: Boolean(isMultimodal),
      isReasoning: Boolean(isReasoning),
    });
  },

  async updateEmbeddingConfig(config: any): Promise<any> {
    const { modelType, providerType, _provider, isMultimodal, ...cleanConfig } = config;
    const provider = providerType || modelType || _provider;

    if (provider === 'default') {
      // Handle default embedding case - just return success
      return { status: 'success' };
    }

    const models = await this.getAllModels('embedding');
    const existingModel = models.find((m) => m.provider === provider && m.isDefault);

    if (existingModel) {
      return this.updateModel('embedding', existingModel.modelKey || existingModel.id, {
        provider,
        configuration: cleanConfig,
        isDefault: true,
        isMultimodal: Boolean(isMultimodal),
      });
    }
    return this.addModel('embedding', {
      provider,
      configuration: cleanConfig,
      isDefault: true,
      isMultimodal: Boolean(isMultimodal),
    });
  },
};

// Export legacy functions for compatibility
export const { getAllModels, addModel, updateModel, deleteModel, setDefaultModel } = modelService;

export const getLlmConfig = modelService.getLlmConfig;
export const getEmbeddingConfig = modelService.getEmbeddingConfig;
export const updateLlmConfig = modelService.updateLlmConfig;
export const updateEmbeddingConfig = modelService.updateEmbeddingConfig;
