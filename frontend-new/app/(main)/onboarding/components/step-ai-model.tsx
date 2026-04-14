'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Flex, Box, Text, Button, TextField, Switch, Spinner } from '@radix-ui/themes';
import { useOnboardingStore } from '../store';
import { getLlmConfig, saveLlmConfig } from '../api';
import type { AiModelFormData, AiProvider, OnboardingStepId } from '../types';

// ── Provider list (mirrors backend llmProvider enum) ────────────────────────

const AI_PROVIDERS: { value: AiProvider; label: string }[] = [
  { value: 'openAI', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'groq', label: 'Groq' },
  { value: 'mistral', label: 'Mistral AI' },
  { value: 'cohere', label: 'Cohere' },
  { value: 'xai', label: 'xAI (Grok)' },
  { value: 'fireworks', label: 'Fireworks AI' },
  { value: 'together', label: 'Together AI' },
  { value: 'azureOpenAI', label: 'Azure OpenAI' },
  { value: 'azureAI', label: 'Azure AI (Foundry)' },
  { value: 'ollama', label: 'Ollama (Local)' },
  { value: 'openAICompatible', label: 'OpenAI-Compatible' },
  { value: 'bedrock', label: 'AWS Bedrock' },
  { value: 'vertexAI', label: 'Google Vertex AI' },
];

// Suggested model names per provider (user can also type their own)
const MODEL_SUGGESTIONS: Record<string, string[]> = {
  openAI: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo', 'o1', 'o1-mini'],
  anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229'],
  gemini: ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash', 'gemini-pro'],
  groq: ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768'],
  mistral: ['mistral-large-latest', 'mistral-small-latest', 'open-mistral-7b'],
  cohere: ['command-r-plus', 'command-r', 'command'],
  xai: ['grok-2', 'grok-2-vision', 'grok-beta'],
  fireworks: ['accounts/fireworks/models/llama-v3p1-70b-instruct'],
  together: ['meta-llama/Llama-3-70b-chat-hf', 'mistralai/Mixtral-8x7B-Instruct-v0.1'],
  azureOpenAI: ['gpt-4o', 'gpt-4-turbo', 'gpt-35-turbo'],
  azureAI: ['Phi-3-medium-4k-instruct', 'Meta-Llama-3-70B-Instruct'],
  ollama: ['llama3.2', 'llama3.1', 'mistral', 'phi3', 'codellama'],
  openAICompatible: [],
  bedrock: ['anthropic.claude-3-5-sonnet-20241022-v2:0', 'meta.llama3-70b-instruct-v1:0'],
  vertexAI: ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash'],
};

// Which providers need specific optional/required fields
const NEEDS_ENDPOINT = new Set<AiProvider>(['azureOpenAI', 'azureAI', 'ollama', 'openAICompatible']);
const NEEDS_DEPLOYMENT = new Set<AiProvider>(['azureOpenAI']);
const NEEDS_API_VERSION = new Set<AiProvider>(['azureOpenAI']);
const API_KEY_OPTIONAL = new Set<AiProvider>(['ollama']);
// Endpoint is shown for these providers but is not required (has a usable default)
const ENDPOINT_OPTIONAL = new Set<AiProvider>(['ollama']);

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

interface StepAiModelProps {
  onSuccess: (nextStep: OnboardingStepId | null) => void;
  systemStepIndex: number;
  totalSystemSteps: number;
}

export function StepAiModel({
  onSuccess,
  systemStepIndex,
  totalSystemSteps,
}: StepAiModelProps) {
  const { aiModel, setAiModel, markStepCompleted, unmarkStepCompleted, submitting, setSubmitting, setSubmitStatus } =
    useOnboardingStore();

  const [form, setForm] = useState<AiModelFormData>({
    provider: aiModel.provider,
    apiKey: aiModel.apiKey,
    model: aiModel.model,
    endpoint: aiModel.endpoint ?? '',
    deploymentName: aiModel.deploymentName ?? '',
    apiVersion: aiModel.apiVersion ?? '',
    modelFriendlyName: aiModel.modelFriendlyName ?? '',
    isReasoning: aiModel.isReasoning,
    isMultimodal: aiModel.isMultimodal,
    contextLength: aiModel.contextLength,
  });

  const [showApiKey, setShowApiKey] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const isDirtyRef = useRef(false);

  // Pre-populate with existing LLM config on mount
  useEffect(() => {
    getLlmConfig()
      .then(({ models }) => {
        const defaultModel = models.find((m) => m.isDefault) ?? models[0];
        if (defaultModel) {
          setForm((prev) => ({
            ...prev,
            provider: (defaultModel.provider as AiProvider) || prev.provider,
            apiKey: defaultModel.configuration.apiKey ?? prev.apiKey,
            model: defaultModel.configuration.model ?? prev.model,
            endpoint: defaultModel.configuration.endpoint ?? prev.endpoint,
            deploymentName: defaultModel.configuration.deploymentName ?? prev.deploymentName,
            apiVersion: defaultModel.configuration.apiVersion ?? prev.apiVersion,
            modelFriendlyName: defaultModel.configuration.modelFriendlyName ?? prev.modelFriendlyName,
            isReasoning: defaultModel.isReasoning,
            isMultimodal: defaultModel.isMultimodal,
            contextLength: defaultModel.contextLength,
          }));
        }
      })
      .catch(() => {
        // No existing config — user fills from scratch
      })
      .finally(() => setLoadingConfig(false));
  }, []);

  // Mark step as pre-completed when GET fills all required fields (and user hasn't edited)
  useEffect(() => {
    if (!loadingConfig && !isDirtyRef.current) {
      const needsEp = NEEDS_ENDPOINT.has(form.provider as AiProvider);
      const epOptional = ENDPOINT_OPTIONAL.has(form.provider as AiProvider);
      const needsDep = NEEDS_DEPLOYMENT.has(form.provider as AiProvider);
      const keyOptional = API_KEY_OPTIONAL.has(form.provider as AiProvider);
      const preComplete =
        form.provider !== '' &&
        (keyOptional || form.apiKey.trim() !== '') &&
        form.model.trim() !== '' &&
        (!needsEp || epOptional || (form.endpoint?.trim() ?? '') !== '') &&
        (!needsDep || (form.deploymentName?.trim() ?? '') !== '');
      if (preComplete) {
        markStepCompleted('ai-model');
      }
    }
  }, [form, loadingConfig]);

  const handleChange = <K extends keyof AiModelFormData>(
    field: K,
    value: AiModelFormData[K]
  ) => {
    isDirtyRef.current = true;
    unmarkStepCompleted('ai-model');
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitStatus('loading');
    setAiModel(form);

    try {
      await saveLlmConfig(form);
      setSubmitStatus('success');
      onSuccess(null);
    } catch {
      setSubmitStatus('error');
    } finally {
      setSubmitting(false);
    }
  };

  const needsEndpoint = NEEDS_ENDPOINT.has(form.provider);
  const endpointOptional = ENDPOINT_OPTIONAL.has(form.provider);
  const needsDeployment = NEEDS_DEPLOYMENT.has(form.provider);
  const needsApiVersion = NEEDS_API_VERSION.has(form.provider);
  const apiKeyOptional = API_KEY_OPTIONAL.has(form.provider);

  const suggestions = form.provider ? MODEL_SUGGESTIONS[form.provider] ?? [] : [];
  const datalistId = 'ai-model-suggestions';

  const isFormValid =
    form.provider !== '' &&
    (apiKeyOptional || form.apiKey.trim() !== '') &&
    form.model.trim() !== '' &&
    (!needsEndpoint || endpointOptional || (form.endpoint?.trim() ?? '') !== '') &&
    (!needsDeployment || (form.deploymentName?.trim() ?? '') !== '');

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
          Step {systemStepIndex}/{totalSystemSteps}: Configure AI Model*
        </Text>
      </Box>

      {/* Scrollable fields */}
      <Box className="no-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '24px' }}>
        <Flex direction="column" gap="5">
        {/* Provider + API Key row */}
        <Flex gap="3">
          <Flex direction="column" gap="1" style={{ flex: 1 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              AI Provider*
            </Text>
            <select
              value={form.provider}
              onChange={(e) => {
                handleChange('provider', e.target.value as AiProvider);
                handleChange('model', '');
                handleChange('endpoint', '');
                handleChange('deploymentName', '');
                handleChange('apiVersion', '');
              }}
              disabled={submitting}
              style={selectStyle}
            >
              <option value="">Select a provider</option>
              {AI_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </Flex>

          <Flex direction="column" gap="1" style={{ flex: 1 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              API Key{apiKeyOptional ? '' : '*'}
            </Text>
            <TextField.Root
              type={showApiKey ? 'text' : 'password'}
              placeholder={apiKeyOptional ? 'Optional for local Ollama' : 'Your API key'}
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
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center' }}
                >
                  <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                    {showApiKey ? 'visibility_off' : 'visibility'}
                  </span>
                </button>
              </TextField.Slot>
            </TextField.Root>
          </Flex>
        </Flex>

        {/* Model (free-form with suggestions) */}
        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Model*
          </Text>
          {suggestions.length > 0 && (
            <datalist id={datalistId}>
              {suggestions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          )}
          <TextField.Root
            placeholder="eg: gpt-4o"
            value={form.model}
            onChange={(e) => handleChange('model', e.target.value)}
            disabled={submitting || !form.provider}
            list={suggestions.length > 0 ? datalistId : undefined}
          >
            <TextField.Slot side="left">
              <span className="material-icons-outlined" style={{ fontSize: '14px', color: 'var(--gray-9)' }}>
                smart_toy
              </span>
            </TextField.Slot>
          </TextField.Root>
        </Flex>

        {/* Conditional: Endpoint */}
        {needsEndpoint && (
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Endpoint{apiKeyOptional ? ' (optional)' : '*'}
            </Text>
            <TextField.Root
              placeholder={
                form.provider === 'azureOpenAI'
                  ? 'https://<resource>.openai.azure.com/'
                  : form.provider === 'ollama'
                  ? 'http://localhost:11434 (optional)'
                  : 'https://your-endpoint'
              }
              value={form.endpoint ?? ''}
              onChange={(e) => handleChange('endpoint', e.target.value)}
              disabled={submitting}
            />
          </Flex>
        )}

        {/* Conditional: Deployment Name (Azure OpenAI only) */}
        {needsDeployment && (
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Deployment Name*
            </Text>
            <TextField.Root
              placeholder="eg: my-gpt4o-deployment"
              value={form.deploymentName ?? ''}
              onChange={(e) => handleChange('deploymentName', e.target.value)}
              disabled={submitting}
            />
          </Flex>
        )}

        {/* Conditional: API Version (Azure OpenAI, optional) */}
        {needsApiVersion && (
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              API Version
            </Text>
            <TextField.Root
              placeholder="eg: 2024-08-01-preview"
              value={form.apiVersion ?? ''}
              onChange={(e) => handleChange('apiVersion', e.target.value)}
              disabled={submitting}
            />
          </Flex>
        )}

        {/* Model Compatibilities */}
        <Flex direction="column" gap="2">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Model Compatibilities
          </Text>

          {/* Reasoning toggle */}
          <Flex
            align="center"
            justify="between"
            style={{ padding: '10px 12px', borderRadius: 'var(--radius-2)', border: '1px solid var(--gray-4)' }}
          >
            <Flex align="center" gap="2">
              <span className="material-icons-outlined" style={{ fontSize: '16px', color: 'var(--gray-9)' }}>
                psychology
              </span>
              <Flex direction="column" gap="0">
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Reasoning</Text>
                <Text size="1" style={{ color: 'var(--gray-9)' }}>Enables multi-step reasoning and complex problem solving</Text>
              </Flex>
            </Flex>
            <Switch
              size="1"
              checked={form.isReasoning}
              onCheckedChange={(checked) => handleChange('isReasoning', checked)}
              disabled={submitting}
            />
          </Flex>

          {/* Multimodal toggle */}
          <Flex
            align="center"
            justify="between"
            style={{ padding: '10px 12px', borderRadius: 'var(--radius-2)', border: '1px solid var(--gray-4)' }}
          >
            <Flex align="center" gap="2">
              <span className="material-icons-outlined" style={{ fontSize: '16px', color: 'var(--gray-9)' }}>
                image
              </span>
              <Flex direction="column" gap="0">
                <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>Multimodal</Text>
                <Text size="1" style={{ color: 'var(--gray-9)' }}>Accepts and understands both text and images</Text>
              </Flex>
            </Flex>
            <Switch
              size="1"
              checked={form.isMultimodal}
              onCheckedChange={(checked) => handleChange('isMultimodal', checked)}
              disabled={submitting}
            />
          </Flex>
        </Flex>

        </Flex>
      </Box>

      {/* Fixed footer: Save button */}
      <Box style={{ flexShrink: 0, padding: '0 24px 24px' }}>
        <Button
          onClick={handleSubmit}
          disabled={!isFormValid || submitting}
          style={{
            width: '100%',
            backgroundColor: !isFormValid || submitting ? 'var(--gray-4)' : 'var(--accent-9)',
            color: !isFormValid || submitting ? 'var(--gray-9)' : 'white',
            cursor: !isFormValid || submitting ? 'not-allowed' : 'pointer',
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

