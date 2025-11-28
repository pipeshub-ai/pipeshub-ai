export interface Model {
  modelType: string;
  provider: string;
  modelName: string;
  modelKey: string;
  isMultimodal: boolean;
  isDefault: boolean;
}

export interface ChatMode {
  id: string;
  name: string;
  description: string;
}

