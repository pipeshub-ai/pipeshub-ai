export interface Model {
  modelType: string;
  provider: string;
  modelName: string;
  modelKey: string;
  isMultimodal: boolean;
  isDefault: boolean;
  modelFriendlyName?: string;
}

export interface ChatMode {
  id: string;
  name: string;
  description: string;
}

