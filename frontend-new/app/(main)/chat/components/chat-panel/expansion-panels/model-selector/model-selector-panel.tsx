'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Flex, Text, Badge, Spinner, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatApi } from '@/chat/api';
import { AgentsApi } from '@/app/(main)/agents/api';
import {
  PROVIDER_FRIENDLY_NAMES,
  MODEL_DESCRIPTIONS,
} from '@/chat/constants';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { toIconPath } from '@/lib/utils/formatters';
import type { AvailableLlmModel, ModelOverride } from '@/chat/types';

interface ModelSelectorPanelProps {
  /** Currently selected model override (null = use default from API) */
  selectedModel: ModelOverride | null;
  /** Called when the user picks a model */
  onModelSelect: (model: ModelOverride) => void;
  /** Hide the "Configured Models / Open Settings" header (used when embedded in a bottom sheet that provides its own header) */
  hideHeader?: boolean;
  /** Optional agent ID - when provided, shows only agent-configured models */
  agentId?: string | null;
}

function ModelLogo({ provider }: { provider: string }) {
  const [hasError, setHasError] = useState(false);

  if (hasError) {
    return (
      <ChatStarIcon
        size={20}
        color="var(--accent-11)"
        style={{ borderRadius: 'var(--radius-1)' }}
      />
    );
  }

  return (
    <Image
      src={toIconPath('logos', provider)}
      alt={provider}
      width={20}
      height={20}
      onError={() => setHasError(true)}
      style={{ flexShrink: 0, borderRadius: 'var(--radius-1)' }}
    />
  );
}

export function ModelSelectorPanel({
  selectedModel,
  onModelSelect,
  hideHeader = false,
  agentId,
}: ModelSelectorPanelProps) {
  const { t } = useTranslation();
  const router = useRouter();

  const [models, setModels] = useState<AvailableLlmModel[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);

    const fetchModels = async () => {
      try {
        if (agentId?.trim()) {
          const { agent } = await AgentsApi.getAgent(agentId);
          if (cancelled) return;

          if (!agent?.models || agent.models.length === 0) {
            setError(
              'This agent has no models configured. Please configure models in the agent builder.'
            );
            setModels([]);
            return;
          }

          setModels(agent.models as AvailableLlmModel[]);
        } else {
          const orgModels = await ChatApi.fetchAvailableLlms();
          if (cancelled) return;

          if (orgModels.length === 0) {
            setError('No models available. Please contact your administrator.');
            setModels([]);
            return;
          }

          setModels(orgModels);
        }
      } catch (err) {
        if (cancelled) return;
        console.error('Failed to fetch models:', err);
        const errorMessage = agentId?.trim()
          ? 'Failed to load agent configuration. Please try again.'
          : 'Failed to load available models. Please try again.';
        setError(errorMessage);
        setModels([]);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    fetchModels();

    return () => {
      cancelled = true;
    };
  }, [agentId]);

  // Auto-select default model when models are loaded and no model is selected
  useEffect(() => {
    if (!selectedModel && models.length > 0) {
      const defaultModel = models.find((m) => m.isDefault);
      if (defaultModel) {
        onModelSelect({
          modelKey: defaultModel.modelKey,
          modelName: defaultModel.modelName,
          modelFriendlyName: defaultModel.modelFriendlyName || defaultModel.modelName,
          modelProvider: defaultModel.provider,
        });
      }
    }
  }, [models, selectedModel, onModelSelect]);

  const handleSelect = useCallback(
    (model: AvailableLlmModel) => {
      onModelSelect({
        modelKey: model.modelKey,
        modelName: model.modelName,
        modelFriendlyName: model.modelFriendlyName,
        modelProvider: model.provider,
      });
    },
    [onModelSelect]
  );

  // Determine which model is "active" — match on both modelKey and modelName
  // because comma-separated configs share the same modelKey.
  const activeKey = selectedModel?.modelKey ?? null;
  const activeName = selectedModel?.modelName ?? null;

  return (
    <Flex direction="column" gap="4" style={{ flex: 1, overflow: 'hidden' }}>
      {/* Header — matches QueryModePanel "Different Modes of Query" style */}
      {!hideHeader && (
        <Flex align="center" justify="between">
          <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('chat.configuredModels', 'Configured Models')}
          </Text>
          <span
            onClick={() => {
              // TODO: navigate to settings page
            }}
            style={{
              fontSize: 'var(--font-size-1)',
              fontWeight: 'var(--font-weight-medium)',
              color: 'var(--slate-11)',
              cursor: 'pointer',
              background: 'none',
              border: '1px solid var(--slate-7)',
              borderRadius: 'var(--radius-2)',
              padding: '2px var(--space-2)',
              lineHeight: 'inherit',
            }}
          >
            {t('chat.openSettings', 'Open Settings')}
          </span>
        </Flex>
      )}

      {/* Body */}
      <Flex
        direction="column"
        gap="2"
        style={{ flex: 1, overflowY: 'auto' }}
        className="no-scrollbar"
      >
        {isLoading && (
          <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
            <Spinner size="2" />
          </Flex>
        )}

        {!isLoading && error && (
          <Flex 
            direction="column" 
            align="center" 
            justify="center" 
            gap="3"
            style={{ padding: 'var(--space-6)' }}
          >
            <MaterialIcon 
              name="error_outline" 
              size={32} 
              color="var(--red-9)" 
            />
            <Text 
              size="2" 
              style={{ 
                color: 'var(--red-9)', 
                textAlign: 'center',
                maxWidth: '300px',
                lineHeight: '1.5'
              }}
            >
              {error}
            </Text>
            {error.includes('no models configured') && agentId && (
              <Button 
                variant="soft" 
                size="2"
                onClick={() => {
                  router.push(`/agents/edit?agentKey=${encodeURIComponent(agentId)}`);
                }}
              >
                <MaterialIcon name="settings" size={16} />
                Configure Models
              </Button>
            )}
          </Flex>
        )}

        {!isLoading && !error && models.map((model) => (
          <ModelItem
            key={`${model.modelKey}::${model.modelName}`}
            model={model}
            isSelected={model.modelKey === activeKey && model.modelName === activeName}
            onSelect={handleSelect}
          />
        ))}
      </Flex>
    </Flex>
  );
}

// ─── Individual model item (card style matching QueryModePanel) ──────

interface ModelItemProps {
  model: AvailableLlmModel;
  isSelected: boolean;
  onSelect: (model: AvailableLlmModel) => void;
}

function ModelItem({ model, isSelected, onSelect }: ModelItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const providerName = PROVIDER_FRIENDLY_NAMES[model.provider] ?? 'Missing Provider Friendly Name';
  const description = MODEL_DESCRIPTIONS[model.modelName] ?? 'Missing model one liner';

  return (
    <Flex
      align="center"
      justify="between"
      onClick={() => onSelect(model)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-1)',
        border: '1px solid var(--olive-3)',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        cursor: 'pointer',
        transition: 'background-color 0.12s ease',
      }}
    >
      {/* Left: all content left-aligned */}
      <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
        {/* Name row: logo + friendly name + dot + provider */}
        <Flex align="center" gap="2">
          <ModelLogo provider={model.provider} />
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {model.modelFriendlyName || model.modelName}
          </Text>
          <Image
            src="/icons/common/ellipse-1.svg"
            alt=""
            width={4}
            height={4}
            style={{ flexShrink: 0 }}
          />
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            by {providerName}
          </Text>
        </Flex>

        {/* Description */}
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '1.4' }}>
          {description}
        </Text>

        {/* Tags */}
        <Flex align="center" gap="1" wrap="wrap" style={{ marginTop: 'var(--space-1)' }}>
          {model.isDefault && (
            <Badge size="1" variant="outline" color="jade">
              Default
            </Badge>
          )}
          {model.isReasoning && (
            <Badge size="1" variant="outline" color="violet">
              Reasoning
            </Badge>
          )}
          {model.isMultimodal && (
            <Badge size="1" variant="outline" color="blue">
              Multimodal
            </Badge>
          )}
        </Flex>
      </Flex>

      {/* Right: Radio indicator */}
      <Flex
        align="center"
        justify="center"
        style={{
          width: '16px',
          height: '16px',
          borderRadius: '50%',
          border: isSelected
            ? '2px solid var(--accent-9)'
            : '1px solid var(--slate-7)',
          flexShrink: 0,
          marginLeft: 'var(--space-3)',
        }}
      >
        {isSelected && (
          <Image
            src="/icons/common/ellipse.svg"
            alt="selected"
            width={16}
            height={20}
            style={{ display: 'block' }}
          />
        )}
      </Flex>
    </Flex>
  );
}
