'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Flex, Box, Text, Button, TextField, Switch, Spinner } from '@radix-ui/themes';
import { InfoBanner } from './info-banner';
import { useOnboardingStore } from '../store';
import { getEmbeddingConfig, saveEmbeddingConfig } from '../api';
import type {
  EmbeddingModelFormData,
  EmbeddingProviderType,
  OnboardingStepId,
} from '../types';

// Mirrors backend embeddingProvider enum
const EMBEDDING_PROVIDERS: { value: EmbeddingProviderType; label: string }[] = [
  { value: 'default', label: 'Default (System Provided)' },
  { value: 'openAI', label: 'OpenAI' },
  { value: 'azureOpenAI', label: 'Azure OpenAI' },
  { value: 'cohere', label: 'Cohere' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'mistral', label: 'Mistral AI' },
  { value: 'voyage', label: 'Voyage AI' },
  { value: 'jinaAI', label: 'Jina AI' },
  { value: 'together', label: 'Together AI' },
  { value: 'bedrock', label: 'AWS Bedrock' },
  { value: 'vertexAI', label: 'Google Vertex AI' },
  { value: 'ollama', label: 'Ollama (Local)' },
  { value: 'openAICompatible', label: 'OpenAI-Compatible' },
  { value: 'sentenceTransformers', label: 'Sentence Transformers' },
  { value: 'fastembed', label: 'FastEmbed' },
];

const EMBEDDING_MODELS_BY_PROVIDER: Record<string, string[]> = {
  openAI: ['text-embedding-3-large', 'text-embedding-3-small', 'text-embedding-ada-002'],
  azureOpenAI: ['text-embedding-3-large', 'text-embedding-ada-002'],
  cohere: ['embed-english-v3.0', 'embed-multilingual-v3.0'],
  gemini: ['text-embedding-004', 'embedding-001'],
  mistral: ['mistral-embed'],
  voyage: ['voyage-3', 'voyage-3-lite', 'voyage-code-2'],
  jinaAI: ['jina-embeddings-v3', 'jina-embeddings-v2-base-en'],
  together: ['togethercomputer/m2-bert-80M-8k-retrieval'],
  ollama: ['nomic-embed-text', 'mxbai-embed-large', 'all-minilm'],
  sentenceTransformers: ['all-MiniLM-L6-v2', 'all-mpnet-base-v2'],
  fastembed: ['BAAI/bge-small-en-v1.5', 'BAAI/bge-base-en-v1.5'],
};

// Providers that need an endpoint URL
const NEEDS_ENDPOINT = new Set<EmbeddingProviderType>(['ollama', 'openAICompatible']);

const selectStyle: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  color: 'var(--gray-12)',
  border: '1px solid var(--gray-5)',
  borderRadius: 'var(--radius-2)',
  padding: '0 8px',
  height: '36px',
  fontSize: '14px',
  width: '100%',
  outline: 'none',
  appearance: 'auto',
};

interface StepEmbeddingModelProps {
  onSuccess: (nextStep: OnboardingStepId | null) => void;
  systemStepIndex: number;
  totalSystemSteps: number;
}

export function StepEmbeddingModel({
  onSuccess,
  systemStepIndex,
  totalSystemSteps,
}: StepEmbeddingModelProps) {
  const { embeddingModel, setEmbeddingModel, markStepCompleted, unmarkStepCompleted, submitting, setSubmitting, setSubmitStatus } =
    useOnboardingStore();

  const [form, setForm] = useState<EmbeddingModelFormData>({
    providerType: embeddingModel.providerType,
    apiKey: embeddingModel.apiKey,
    model: embeddingModel.model,
    endpoint: embeddingModel.endpoint ?? '',
    isMultimodal: embeddingModel.isMultimodal,
  });

  const [showApiKey, setShowApiKey] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const isDirtyRef = useRef(false);

  // Mark step as pre-completed when GET data is available (and user hasn't edited)
  useEffect(() => {
    if (!loadingConfig && !isDirtyRef.current) {
      const isCustom = form.providerType !== 'default' && form.providerType !== '';
      const preComplete =
        !isCustom ||
        (form.apiKey.trim() !== '' && form.model.trim() !== '');
      if (preComplete) {
        markStepCompleted('embedding-model');
      }
    }
  }, [form, loadingConfig]);

  // Pre-populate with existing embedding config on mount
  useEffect(() => {
    getEmbeddingConfig()
      .then(({ models }) => {
        const defaultModel = models.find((m) => m.isDefault) ?? models[0];
        if (defaultModel && defaultModel.provider !== 'default') {
          setForm((prev) => ({
            ...prev,
            providerType: (defaultModel.provider as EmbeddingProviderType) || prev.providerType,
            apiKey: defaultModel.configuration.apiKey ?? prev.apiKey,
            model: defaultModel.configuration.model ?? prev.model,
            endpoint: defaultModel.configuration.endpoint ?? prev.endpoint,
            isMultimodal: defaultModel.isMultimodal,
          }));
        }
      })
      .catch(() => {})
      .finally(() => setLoadingConfig(false));
  }, []);

  const handleChange = <K extends keyof EmbeddingModelFormData>(
    field: K,
    value: EmbeddingModelFormData[K]
  ) => {
    isDirtyRef.current = true;
    unmarkStepCompleted('embedding-model');
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitStatus('loading');
    setEmbeddingModel(form);

    try {
      await saveEmbeddingConfig(form);
      setSubmitStatus('success');
      onSuccess(null);
    } catch {
      setSubmitStatus('error');
    } finally {
      setSubmitting(false);
    }
  };

  const needsCustomProvider = form.providerType !== 'default' && form.providerType !== '';
  const needsEndpoint = NEEDS_ENDPOINT.has(form.providerType);

  // Client-side validation: default provider always valid; custom requires key + model
  const isFormValid =
    !needsCustomProvider ||
    (
      form.apiKey.trim() !== '' &&
      form.model.trim() !== '' &&
      (!needsEndpoint || (form.endpoint?.trim() ?? '') !== '')
    );

  const suggestions = form.providerType
    ? EMBEDDING_MODELS_BY_PROVIDER[form.providerType] ?? []
    : [];
  const datalistId = 'embed-model-suggestions';

  if (loadingConfig) {
    return (
      <Flex align="center" justify="center" style={{ height: '200px' }}>
        <Spinner size="2" />
      </Flex>
    );
  }

  return (
    <Box
      style={{
        backgroundColor: 'var(--gray-2)',
        border: '1px solid var(--gray-4)',
        borderRadius: 'var(--radius-3)',
        width: '576px',
        maxHeight: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* Fixed Sub-header */}
      <Box
        style={{
          flexShrink: 0,
          padding: '24px 24px 16px',
          borderBottom: '1px solid var(--gray-4)',
        }}
      >
        <Text
          as="div"
          size="1"
          style={{ color: 'var(--gray-9)', marginBottom: '4px', letterSpacing: '0.02em' }}
        >
          System Configuration
        </Text>
        <Text
          as="div"
          size="4"
          weight="bold"
          style={{ color: 'var(--gray-12)' }}
        >
          Step {systemStepIndex}/{totalSystemSteps}: Configure Embedding Model*
        </Text>
      </Box>

      {/* Scrollable fields */}
      <Box className="no-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}>
        <Flex direction="column" gap="6">
        <InfoBanner message="Select an embedding provider: default system embeddings or a specific one. You can change this later in Workspace settings." />
        {/* Provider Type (full row when default, side-by-side with API Key when custom) */}
        {!needsCustomProvider ? (
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Provider Type*
            </Text>
            <select
              value={form.providerType}
              onChange={(e) => {
                handleChange('providerType', e.target.value as EmbeddingProviderType);
                handleChange('model', '');
              }}
              disabled={submitting}
              style={selectStyle}
            >
              {EMBEDDING_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Flex>
        ) : (
          <Flex gap="3">
            <Flex direction="column" gap="1" style={{ flex: 1 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                Embeddings Provider*
              </Text>
              <select
                value={form.providerType}
                onChange={(e) => {
                  handleChange('providerType', e.target.value as EmbeddingProviderType);
                  handleChange('model', '');
                }}
                disabled={submitting}
                style={selectStyle}
              >
                {EMBEDDING_PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </Flex>
            <Flex direction="column" gap="1" style={{ flex: 1 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                API Key*
              </Text>
              <TextField.Root
                type={showApiKey ? 'text' : 'password'}
                placeholder="Your API Key"
                value={form.apiKey}
                onChange={(e) => handleChange('apiKey', e.target.value)}
                disabled={submitting}
              >
                <TextField.Slot side="left">
                  <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                    vpn_key
                  </span>
                </TextField.Slot>
                <TextField.Slot side="right">
                  <button
                    type="button"
                    onClick={() => setShowApiKey((v) => !v)}
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      padding: '0',
                      display: 'flex',
                      alignItems: 'center',
                    }}
                  >
                    <span
                      className="material-icons-outlined"
                      style={{ fontSize: '14px', color: 'var(--gray-9)' }}
                    >
                      {showApiKey ? 'visibility_off' : 'visibility'}
                    </span>
                  </button>
                </TextField.Slot>
              </TextField.Root>
            </Flex>
          </Flex>
        )}

        {/* Custom provider fields */}
        {needsCustomProvider && (
          <>

            {/* Model */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                Model*
              </Text>
              <datalist id={datalistId}>
                {suggestions.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
              <TextField.Root
                list={datalistId}
                placeholder="e.g. text-embedding-3-small"
                value={form.model}
                onChange={(e) => handleChange('model', e.target.value)}
                disabled={submitting}
              >
                <TextField.Slot side="left">
                  <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                    smart_toy
                  </span>
                </TextField.Slot>
              </TextField.Root>
            </Flex>

            {/* Endpoint (only for providers that need a custom URL) */}
            {needsEndpoint && (
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                  Endpoint URL*
                </Text>
                <TextField.Root
                  placeholder="https://your-endpoint/v1"
                  value={form.endpoint ?? ''}
                  onChange={(e) => handleChange('endpoint', e.target.value)}
                  disabled={submitting}
                >
                  <TextField.Slot side="left">
                    <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                      link
                    </span>
                  </TextField.Slot>
                </TextField.Root>
              </Flex>
            )}

            {/* Multimodal toggle */}
            <Box>
              <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', marginBottom: '8px', display: 'block' }}>
                Model Compatibilities*
              </Text>
              <Flex
                align="center"
                justify="between"
                style={{
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-2)',
                  border: '1px solid var(--gray-4)',
                }}
              >
                <Flex align="center" gap="2">
                  <span
                    className="material-icons-outlined"
                    style={{ fontSize: '16px', color: 'var(--gray-9)' }}
                  >
                    image
                  </span>
                  <Flex direction="column" gap="0">
                    <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                      Multimodal
                    </Text>
                    <Text size="1" style={{ color: 'var(--gray-9)' }}>
                      Accepts and understands both text and images
                    </Text>
                  </Flex>
                </Flex>
                <Switch
                  size="1"
                  checked={form.isMultimodal}
                  onCheckedChange={(checked) => handleChange('isMultimodal', checked)}
                  disabled={submitting}
                />
              </Flex>
            </Box>
          </>
        )}

        </Flex>
      </Box>

      {/* Fixed footer: Save button */}
      <Box style={{ flexShrink: 0, padding: '0 24px 24px' }}>
        <Button
          onClick={handleSubmit}
          disabled={submitting || !isFormValid}
          style={{
            width: '100%',
            backgroundColor: submitting || !isFormValid ? 'var(--gray-4)' : 'var(--accent-9)',
            color: submitting || !isFormValid ? 'var(--gray-9)' : 'white',
            cursor: submitting || !isFormValid ? 'not-allowed' : 'pointer',
            height: '40px',
            opacity: 1,
          }}
        >
          {submitting ? (
            <Flex align="center" gap="2">
              <Spinner size="1" />
              Saving…
            </Flex>
          ) : (
            'Save'
          )}
        </Button>
      </Box>
    </Box>
  );
}
