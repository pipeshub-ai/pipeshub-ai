// types.ts
import type { Icon as IconifyIcon } from '@iconify/react';

import modelAltIcon from '@iconify-icons/carbon/model-alt';
import robotOutlineIcon from '@iconify-icons/mdi/robot-outline';
import modelTrainingIcon from '@iconify-icons/material-symbols/model-training';
import machineLearningModelIcon from '@iconify-icons/carbon/machine-learning-model';
import watsonMachineLearningIcon from '@iconify-icons/carbon/watson-machine-learning';
import documentScanIcon from '@iconify-icons/material-symbols/document-scanner-outline';

export interface ModelConfig {
  name: string;
  configuration: Record<string, any>;
}

export interface AiModel {
  type: string;
  enabled: boolean;
  configurations: ModelConfig[];
}

export interface LlmConfig {
  modelType: 'openai' | 'azure';
  apiKey: string;
  model: string;
  clientId?: string;
  endpoint?: string;
  deploymentName?: string;
}

export interface OpenAILlmConfig {
  modelType: 'openai';
  clientId: string;
  apiKey: string;
  model: string;
}

export interface AzureLlmConfig {
  modelType: 'azure';
  endpoint: string;
  apiKey: string;
  deploymentName: string;
  model: string;
}

export interface OCRConfig {
  name: string;
  apiKey: string;
  endpoint?: string;
}

export interface EmbeddingConfig {
  name: string;
  apiKey: string;
  model: string;
  endpoint?: string;
}

export interface SLMConfig {
  name: string;
  apiKey: string;
  model: string;
}

export interface ReasoningConfig {
  name: string;
  apiKey: string;
  model: string;
}

export interface MultiModalConfig {
  name: string;
  apiKey: string;
  model: string;
  endpoint?: string;
}

// Model providers by type
export const MODEL_PROVIDERS = {
  llm: ['OpenAI', 'Azure OpenAI', 'Anthropic', 'Google AI', 'Cohere'],
  ocr: ['Azure Document Intelligence', 'Google Document AI', 'Amazon Textract'],
  embedding: ['OpenAI', 'Azure OpenAI', 'Google AI', 'Cohere'],
  slm: ['OpenAI', 'Google AI', 'Anthropic', 'Mistral AI'],
  reasoning: ['OpenAI', 'Anthropic', 'Google AI'],
  multiModal: ['OpenAI', 'Google AI', 'Anthropic', 'Claude'],
};

// Human-readable model type names
export const MODEL_TYPE_NAMES: {
  [key: string]: string;
  llm: string;
  ocr: string;
  embedding: string;
  slm: string;
  reasoning: string;
  multiModal: string;
} = {
  llm: 'Large Language Models',
  ocr: 'Optical Character Recognition',
  embedding: 'Embedding Models',
  slm: 'Small Language Models',
  reasoning: 'Reasoning Models',
  multiModal: 'Multi-Modal Models',
};

export const MODEL_TYPE_DESCRIPTIONS: {
  [key: string]: string;
  llm: string;
  ocr: string;
  embedding: string;
  slm: string;
  reasoning: string;
  multiModal: string;
} = {
  llm: 'Powerful text generation and comprehension models',
  ocr: 'Extract text from documents and images',
  embedding: 'Convert text to numerical vectors for semantic search',
  slm: 'Lightweight language models for simpler tasks',
  reasoning: 'Advanced models with analytical capabilities',
  multiModal: 'Process multiple inputs like text, images, and audio',
};

// And for MODEL_TYPE_ICONS
export const MODEL_TYPE_ICONS: {
  [key: string]: React.ComponentProps<typeof IconifyIcon>['icon'];
  llm: React.ComponentProps<typeof IconifyIcon>['icon'];
  ocr: React.ComponentProps<typeof IconifyIcon>['icon'];
  embedding: React.ComponentProps<typeof IconifyIcon>['icon'];
  slm: React.ComponentProps<typeof IconifyIcon>['icon'];
  reasoning: React.ComponentProps<typeof IconifyIcon>['icon'];
  multiModal: React.ComponentProps<typeof IconifyIcon>['icon'];
} = {
  llm: machineLearningModelIcon,
  ocr: documentScanIcon,
  embedding: watsonMachineLearningIcon,
  slm: robotOutlineIcon,
  reasoning: modelAltIcon,
  multiModal: modelTrainingIcon,
};
