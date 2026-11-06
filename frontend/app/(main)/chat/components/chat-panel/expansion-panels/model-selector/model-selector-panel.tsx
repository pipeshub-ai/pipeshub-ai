'use client';

import { useEffect, useState, useCallback } from 'react';
import { Flex, Text, Badge, RadioGroup, Button } from '@radix-ui/themes';
import { ToggleGroup } from 'radix-ui';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Link } from '@/lib/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { touchTarget } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useChatStore, ctxKeyFromAgent, ASSISTANT_CTX } from '@/chat/store';
import { fetchModelsForContext, OrgModelsFetchError } from '@/chat/utils/fetch-models-for-context';
import {
  PROVIDER_FRIENDLY_NAMES,
  MODEL_DESCRIPTIONS,
  humanizeProviderKey,
} from '@/chat/constants';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { resolveLlmProviderIconPath, AGENT_LLM_FALLBACK_ICON } from '@/lib/utils/llm-provider-icons';
import {
  DEFAULT_REASONING_EFFORT,
  normalizeReasoningEffort,
  type AvailableLlmModel,
  type ModelOverride,
  type ReasoningEffort,
} from '@/chat/types';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { ModelSelectorExtraOptions } from '@/config';

// Exported so other chat surfaces (e.g. the chat-input toolbar trigger) can
// render a matching "· High" style indicator without redefining labels.
//
// "none" is intentionally NOT offered here: some reasoning-capable models
// (e.g. ChatOpenAI with reasoning_effort="none") are prone to hallucinating
// malformed/oversized tool-call names once reasoning is fully disabled,
// which can crash the whole turn. The backend still accepts "none" on the
// wire (old persisted conversations/agents may carry it) and silently
// upgrades it to "low" rather than rejecting it — see
// `_reasoning_effort_kwargs` in `app/utils/aimodels.py`.
export const REASONING_EFFORT_OPTIONS: { value: ReasoningEffort; labelKey: string; defaultLabel: string }[] = [
  { value: 'low', labelKey: 'chat.reasoningEffort.low', defaultLabel: 'Low' },
  { value: 'medium', labelKey: 'chat.reasoningEffort.medium', defaultLabel: 'Medium' },
  { value: 'high', labelKey: 'chat.reasoningEffort.high', defaultLabel: 'High' },
  { value: 'max', labelKey: 'chat.reasoningEffort.max', defaultLabel: 'Max' },
];

export function getReasoningEffortLabel(t: TFunction, value: ReasoningEffort): string {
  const normalized = normalizeReasoningEffort(value) ?? value;
  const option = REASONING_EFFORT_OPTIONS.find((o) => o.value === normalized);
  return option ? t(option.labelKey, option.defaultLabel) : normalized;
}

/**
 * Label for the effort a chat runs with. A pick or an agent's default is sent
 * on the request; with neither, the backend applies the model's own default,
 * then `DEFAULT_REASONING_EFFORT`.
 */
export function getAppliedReasoningEffortLabel(
  t: TFunction,
  {
    picked,
    agentDefault = null,
    modelDefault,
  }: {
    picked: ReasoningEffort | null;
    agentDefault?: ReasoningEffort | null;
    modelDefault: ReasoningEffort | null;
  },
): string {
  const chosen = picked ?? agentDefault;
  if (chosen) return getReasoningEffortLabel(t, chosen);
  if (modelDefault) {
    return t('chat.reasoningEffort.modelDefaultLabel', {
      defaultValue: 'Default ({{level}})',
      level: getReasoningEffortLabel(t, modelDefault),
    });
  }
  return getReasoningEffortLabel(t, DEFAULT_REASONING_EFFORT);
}

interface ModelSelectorPanelProps {
  /** Currently selected model override (null = use default from API) */
  selectedModel: ModelOverride | null;
  /** Called when the user picks a model */
  onModelSelect: (model: ModelOverride) => void;
  /** Hide the "Configured Models / Open Settings" header (used when embedded in a bottom sheet that provides its own header) */
  hideHeader?: boolean;
  /** Optional agent ID - when provided, shows only agent-configured models */
  agentId?: string | null;
  /** Show the edition's entries beside the configured models. Off where a configured model must be chosen. */
  allowExtraOptions?: boolean;
}

function ModelLogo({ provider }: { provider: string }) {
  return (
    <ThemeableAssetIcon
      src={resolveLlmProviderIconPath(provider)}
      fallbackSrc={AGENT_LLM_FALLBACK_ICON}
      size={20}
      color="var(--text-primary)"
      variant="flat"
    />
  );
}

export function ModelSelectorPanel({
  selectedModel,
  onModelSelect,
  hideHeader = false,
  agentId,
  allowExtraOptions = true,
}: ModelSelectorPanelProps) {
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);

  const ctxKey = ctxKeyFromAgent(agentId);
  // Read the shared cache so the panel re-renders as soon as the fetcher
  // writes results — no duplicate network calls.
  const cached = useChatStore((s) => s.settings.availableModels[ctxKey]);
  const models: AvailableLlmModel[] = cached?.models ?? [];

  const reasoningEffort = normalizeReasoningEffort(useChatStore((s) => s.settings.reasoningEffort[ctxKey] ?? null));
  const agentDefaultEffort = useChatStore((s) => s.settings.agentDefaultReasoningEffort[ctxKey] ?? null);
  const setReasoningEffortForCtx = useChatStore((s) => s.setReasoningEffortForCtx);

  const [isLoading, setIsLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);
  const loading = useDeferredLoading(isLoading);
  // The skeleton shows only once the hook says so; the list waits until it has gone.
  const showSkeleton = loading.visible;
  const busy = isLoading || loading.visible;

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setIsLoading(!cached);

    // Force a refetch whenever the panel is (re)opened: the set of available
    // models can change between visits (admin adds/removes an LLM, an agent's
    // configuration is edited elsewhere), and clicking the AI Models button
    // is an explicit user signal that they want to see the current list.
    // The util still dedupes concurrent in-flight calls, so this is safe.
    fetchModelsForContext(ctxKey, { force: true })
      .then((fresh) => {
        if (cancelled) return;
        // An agent with no models of its own now falls back to fetching the
        // org-wide list (see `fetchModelsForContext`), so an empty result
        // here always means the organization has no LLMs configured at all
        // — not that this particular agent is missing a model.
        if (fresh.length === 0) {
          setError(t('chat.noModelsAvailable'));
        }
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('Failed to fetch models:', err);
        // An `OrgModelsFetchError` means the agent's own config fetch
        // succeeded (it just has no models) and the org-wide fallback list
        // failed — that's an org-models failure, not an agent-config one.
        const isOrgModelsFailure = ctxKey === ASSISTANT_CTX || err instanceof OrgModelsFetchError;
        setError(
          isOrgModelsFailure
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
        modelFriendlyName: model.modelFriendlyName || model.modelName,
        modelProvider: model.provider,
      });
    },
    [onModelSelect]
  );

  // Determine which model is "active" — match on both modelKey and modelName
  // because comma-separated configs share the same modelKey.
  const activeKey = selectedModel?.modelKey ?? null;
  const activeName = selectedModel?.modelName ?? null;
  const activeModel = models.find(
    (model) => model.modelKey === activeKey && model.modelName === activeName
  );
  const showReasoningEffort = Boolean(activeModel?.isReasoning);
  const modelDefaultEffort = normalizeReasoningEffort(activeModel?.defaultReasoningEffort ?? null);

  const handleReasoningEffortSelect = useCallback(
    (value: ReasoningEffort) => {
      // Pressing the pill that is already picked clears the pick, so the
      // chat goes back to the default rather than staying stuck on it.
      setReasoningEffortForCtx(ctxKey, reasoningEffort === value ? null : value);
    },
    [ctxKey, reasoningEffort, setReasoningEffortForCtx]
  );

  const groupLabel = t('chat.configuredModels', 'Configured models');
  const handleGroupChange = useCallback(
    (value: string) => {
      const model = models.find((m) => modelValue(m) === value);
      if (model) handleSelect(model);
    },
    [models, handleSelect]
  );

  return (
    <Flex direction="column" gap="3" data-testid="chat-model-panel" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
      {!hideHeader && (
        <Flex align="center" justify="between" gap="2">
          <Text size="2" weight="medium" style={{ color: 'var(--text-primary)' }}>
            {groupLabel}
          </Text>
          {isAdmin && !error && (
            <Button asChild size="1" variant="outline" color="gray" radius="small">
              <Link href={AI_MODELS_HREF}>{t('chat.seeMoreModels', 'See more models')}</Link>
            </Button>
          )}
        </Flex>
      )}

      {/* Above the list, so it stays in view however many models there are. */}
      {!busy && !error && showReasoningEffort && (
        <ReasoningEffortSelector
          value={reasoningEffort}
          onSelect={handleReasoningEffortSelect}
          agentDefault={agentDefaultEffort}
          modelDefault={modelDefaultEffort}
        />
      )}

      {showSkeleton && (
        <ListSkeleton
          variant="menu-row"
          // The card height, so nothing moves when the cards arrive.
          rowHeight={MODEL_CARD_HEIGHT}
          glyphSize={20}
          appearDelayMs={0}
          exiting={loading.exiting}
          statusLabel={t('chat.loadingModels', 'Loading models…')}
        />
      )}

      {!busy && error && (
        <Flex direction="column" align="center" justify="center" gap="3" style={{ padding: 'var(--space-6)' }}>
          <MaterialIcon name="error_outline" size={32} color="var(--red-9)" />
          <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center', maxWidth: '300px', lineHeight: '1.5' }}>
            {error}
          </Text>
          {isAdmin && (
            <Button asChild variant="soft" size="2">
              <Link href={AI_MODELS_HREF}>
                <MaterialIcon name="settings" size={16} />
                {t('chat.openModels', 'Manage models')}
              </Link>
            </Button>
          )}
        </Flex>
      )}

      {!busy && !error && allowExtraOptions && (
        <ModelSelectorExtraOptions models={models} selectedModel={selectedModel} onModelSelect={onModelSelect} />
      )}

      {!busy && !error && (
        // One radio group for the whole list: Tab enters it once and the
        // arrow keys move the choice.
        <RadioGroup.Root
          value={activeModel ? modelValue(activeModel) : ''}
          onValueChange={handleGroupChange}
          aria-label={groupLabel}
          className="no-scrollbar"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-2)',
            // 296px: room for four cards before the list scrolls.
            maxHeight: 'calc(var(--space-9) * 4 + var(--space-7))',
            minHeight: 0,
            overflowY: 'auto',
            // The last card's border never sits on the clip edge, where it rounds away at some zoom levels; the
            // inset counts inside the 296px.
            paddingBottom: 'var(--space-1)',
            boxSizing: 'border-box',
          }}
        >
          {models.map((model) => (
            <ModelItem
              key={modelValue(model)}
              model={model}
              isSelected={model === activeModel}
              onSelect={handleSelect}
            />
          ))}
        </RadioGroup.Root>
      )}
    </Flex>
  );
}

/** The admin page where models are added and configured. */
const AI_MODELS_HREF = '/workspace/ai-models/';

/** A model card with one tag row: 12 + 20 + 8 + 20 + 12, plus its 1px border. */
const MODEL_CARD_HEIGHT = 74;

/** Comma-separated configs share a `modelKey`, so the name is part of the value. */
function modelValue(model: AvailableLlmModel): string {
  return `${model.modelKey}::${model.modelName}`;
}

// ─── Reasoning effort, shown only for reasoning models ───

interface ReasoningEffortSelectorProps {
  /** `null` = no explicit override, model/provider uses its own default. */
  value: ReasoningEffort | null;
  onSelect: (value: ReasoningEffort) => void;
  agentDefault: ReasoningEffort | null;
  modelDefault: ReasoningEffort | null;
}

/**
 * One pill per effort level. With nothing picked, the level that applies (the
 * agent's default, then the model's, then the product default) is highlighted.
 * Pressing the picked pill again goes back to the default.
 */
export function ReasoningEffortSelector({ value, onSelect, agentDefault, modelDefault }: ReasoningEffortSelectorProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const effectiveDefault = agentDefault ?? modelDefault ?? DEFAULT_REASONING_EFFORT;
  const applied = value ?? effectiveDefault;
  const usesModelDefault = !agentDefault && Boolean(modelDefault);
  const label = t('chat.reasoningEffort.label', 'Reasoning effort');

  return (
    <Flex
      direction="column"
      gap="2"
      data-testid="reasoning-effort-selector"
      style={{
        flexShrink: 0,
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--border-subtle)',
        // Raised, like the model cards: the shell behind it is the subtle surface while a panel is open.
        backgroundColor: 'var(--surface-raised)',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--text-primary)' }}>
        {label}
      </Text>
      <ToggleGroup.Root
        type="single"
        role="radiogroup"
        aria-label={label}
        value={applied}
        // A press on the pill that is already on sends an empty value: pass
        // that level on, so the parent can clear the pick.
        onValueChange={(next) => onSelect((next || applied) as ReasoningEffort)}
        style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--space-2)' }}
      >
        {REASONING_EFFORT_OPTIONS.map((option) => {
          const isActive = option.value === applied;
          return (
            <ToggleGroup.Item key={option.value} value={option.value} asChild>
              <Button
                type="button"
                size="1"
                radius="full"
                variant={isActive ? 'surface' : 'outline'}
                color={isActive ? undefined : 'gray'}
                // On phones: a 44px touch target that stays a pill (wider than tall).
                style={isMobile ? { ...touchTarget(true), paddingInline: 'var(--space-3)' } : undefined}
              >
                {t(option.labelKey, option.defaultLabel)}
              </Button>
            </ToggleGroup.Item>
          );
        })}
      </ToggleGroup.Root>
      <Text size="1" style={{ color: 'var(--text-secondary)' }}>
        {value
          ? t('chat.reasoningEffort.overrideHint', 'Click again to use the default.')
          : usesModelDefault
            ? t('chat.reasoningEffort.modelDefaultHint', {
                defaultValue: "Defaults to {{level}}, this model's own default, when not set.",
                level: getReasoningEffortLabel(t, effectiveDefault),
              })
            : t('chat.reasoningEffort.defaultHintWithLevel', {
                defaultValue: 'Defaults to {{level}} when not set.',
                level: getReasoningEffortLabel(t, effectiveDefault),
              })}
      </Text>
    </Flex>
  );
}

// ─── One configured model ──────

interface ModelItemProps {
  model: AvailableLlmModel;
  isSelected: boolean;
  onSelect: (model: AvailableLlmModel) => void;
}

/**
 * A card for one model. The whole card selects it; the radio inside is the
 * control keyboards and screen readers use.
 */
function ModelItem({ model, isSelected, onSelect }: ModelItemProps) {
  const { t } = useTranslation();
  // Provider always comes through from the API. If we don't have a curated
  // friendly name for it in PROVIDER_FRIENDLY_NAMES, fall back to splitting
  // the raw camelCase provider key into words rather than a placeholder.
  const providerKey = Object.keys(PROVIDER_FRIENDLY_NAMES).find(
    (k) => k.toLowerCase() === model.provider?.toLowerCase(),
  );
  const providerName = providerKey
    ? PROVIDER_FRIENDLY_NAMES[providerKey]
    : humanizeProviderKey(model.provider?.trim() || '');
  // Description is optional — only render when we actually have one so we
  // don't show placeholder text for models that aren't in the curated map.
  const description = MODEL_DESCRIPTIONS[model.modelName];
  const name = model.modelFriendlyName || model.modelName;

  return (
    <Flex
      direction="column"
      gap="3"
      data-testid="chat-model-option"
      data-selected={isSelected || undefined}
      onClick={() => {
        if (!isSelected) onSelect(model);
      }}
      style={{
        flexShrink: 0,
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        border: `1px solid ${isSelected ? 'var(--accent-a7)' : 'var(--border-default)'}`,
        backgroundColor: isSelected ? 'var(--accent-a2)' : 'var(--surface-raised)',
        cursor: 'pointer',
      }}
    >
      <Flex align="center" justify="between" gap="3">
        <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap="2" wrap="wrap">
            <ModelLogo provider={model.provider} />
            <Text size="2" weight="medium" style={{ color: 'var(--text-primary)' }}>
              {name}
            </Text>
            {providerName && (
              <>
                <Text size="1" aria-hidden style={{ color: 'var(--text-tertiary)' }}>
                  ·
                </Text>
                <Text size="1" style={{ color: 'var(--text-secondary)' }}>
                  {t('chat.modelProviderBy', { provider: providerName, defaultValue: 'by {{provider}}' })}
                </Text>
              </>
            )}
          </Flex>

          {description && (
            <Text size="1" style={{ color: 'var(--text-secondary)' }}>
              {description}
            </Text>
          )}

          {(model.isDefault || model.isReasoning || model.isMultimodal) && (
            <Flex align="center" gap="2" wrap="wrap">
              {model.isDefault && (
                // The accent colour: the workspace's own choice.
                <Badge size="1" variant="soft" radius="small">
                  {t('chat.modelTags.default', 'Default')}
                </Badge>
              )}
              {model.isReasoning && (
                <Badge size="1" variant="soft" radius="small" color="indigo">
                  {t('chat.modelTags.reasoning', 'Reasoning')}
                </Badge>
              )}
              {model.isMultimodal && (
                // Soft blue text is under 4.5:1 on a light card; high contrast lifts it.
                <Badge size="1" variant="soft" radius="small" color="blue" highContrast>
                  {t('chat.modelTags.multimodal', 'Multimodal')}
                </Badge>
              )}
            </Flex>
          )}
        </Flex>

        <RadioGroup.Item value={modelValue(model)} aria-label={name} />
      </Flex>
    </Flex>
  );
}
