'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { Flex, Text, Badge, Spinner, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useChatStore, ctxKeyFromAgent, ASSISTANT_CTX } from '@/chat/store';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
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

  const ctxKey = ctxKeyFromAgent(agentId);
  // Read the shared cache so the panel re-renders as soon as the fetcher
  // writes results — no duplicate network calls.
  const cached = useChatStore((s) => s.settings.availableModels[ctxKey]);
  const models: AvailableLlmModel[] = cached?.models ?? [];

  const [isLoading, setIsLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setIsLoading(!cached);

    fetchModelsForContext(ctxKey)
      .then((fresh) => {
        if (cancelled) return;
        if (fresh.length === 0) {
          setError(
            ctxKey === ASSISTANT_CTX
              ? t('chat.noModelsAvailable')
              : t('chat.agentNoModelsConfigured'),
          );
        }
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to fetch models:', err);
        setError(
          ctxKey === ASSISTANT_CTX
            ? t('chat.failedToLoadModels')
            : t('chat.failedToLoadAgentConfig'),
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // `cached` intentionally excluded — including it would force a refetch
    // every time the cache writes back, defeating the dedupe in the util.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctxKey, t]);

  // NOTE: We intentionally do NOT auto-select the default model here.
  // The chat-input pill already falls back to `defaultModels[ctxKey]` when
  // `selectedModels[ctxKey]` is null, so there is no need to mutate the
  // user's selection slot. Writing default into the selection on mount used
  // to leak between contexts (e.g. picking default in assistant silently
  // locked that model into every agent the user visited).

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
            {error === t('chat.agentNoModelsConfigured') && agentId && (
              <Button 
                variant="soft" 
                size="2"
                onClick={() => {
                  router.push(`/agents/edit?agentKey=${encodeURIComponent(agentId)}`);
                }}
              >
                <MaterialIcon name="settings" size={16} />
                {t('chat.configureModels')}
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
