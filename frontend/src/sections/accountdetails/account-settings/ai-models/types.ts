// types.ts
import type { Icon as IconifyIcon } from '@iconify/react';

import searchIcon from '@iconify-icons/mdi/magnify';
import modelAltIcon from '@iconify-icons/carbon/model-alt';
import robotOutlineIcon from '@iconify-icons/mdi/robot-outline';
import modelTrainingIcon from '@iconify-icons/material-symbols/model-training';
import machineLearningModelIcon from '@iconify-icons/carbon/machine-learning-model';
import documentScanIcon from '@iconify-icons/material-symbols/document-scanner-outline'; // Added for embedding

export interface ModelConfig {
  name: string;
  configuration: Record<string, any>;
}

export interface AiModel {
  type: string;
  enabled: boolean;
  configurations: ModelConfig[];
}


// Model providers by type
export const MODEL_PROVIDERS = {
  llm: ['OpenAI', 'Azure OpenAI', 'Anthropic', 'Google AI', 'Cohere'],
  ocr: ['Azure Document Intelligence', 'Google Document AI', 'Amazon Textract'],
  embedding: ['OpenAI', 'Azure OpenAI', 'Default (System Provided)'],
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
  embedding: 'Convert text to numerical vectors for semantic search and document retrieval',
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
  embedding: searchIcon, // Updated to use search icon for embeddings
  slm: robotOutlineIcon,
  reasoning: modelAltIcon,
  multiModal: modelTrainingIcon,
};