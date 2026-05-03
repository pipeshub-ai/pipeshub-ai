'use client';

import type { TFunction } from 'i18next';
import React, { useMemo, useState } from 'react';
import { Flex, Popover, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { aiModelsCapabilityLabel } from '../capability-i18n';
import type { AIModelProvider, ConfiguredModel } from '../types';
import type { CapabilitySection } from '../types';
import { LLM_SECTION_MODEL_TYPES, registryCapabilityForModelType } from '../types';
import { MODEL_ROW_ICON_CONTAINER_STYLE, modelRowCardStyle } from './model-row-layout';

const EMBEDDING_BUILTIN_PLACEHOLDER_MODEL_KEY = 'builtin-default' as const;

/** Shown when no embedding models are configured; matches backend built-in default shape. */
const EMBEDDING_BUILTIN_PLACEHOLDER: ConfiguredModel = {
  modelKey: EMBEDDING_BUILTIN_PLACEHOLDER_MODEL_KEY,
  provider: 'default',
  modelType: 'embedding',
  configuration: { model: 'default' },
  isMultimodal: false,
  isReasoning: false,
  isDefault: false,
  contextLength: null,
};

function isEmbeddingBuiltinPlaceholder(model: ConfiguredModel): boolean {
  return (
    model.modelKey === EMBEDDING_BUILTIN_PLACEHOLDER_MODEL_KEY &&
    model.modelType === 'embedding' &&
    model.provider === 'default' &&
    model.configuration?.model === 'default'
  );
}

function modelTypesForSection(section: CapabilitySection): readonly string[] {
  if (section === 'text_generation') return LLM_SECTION_MODEL_TYPES;
  if (section === 'embedding') return ['embedding'];
  if (section === 'tts') return ['tts'];
  if (section === 'stt') return ['stt'];
  return ['imageGeneration'];
}

function capabilityLabelForModelType(mt: string, t: TFunction): string {
  const cap = registryCapabilityForModelType(mt);
  return aiModelsCapabilityLabel(t, cap);
}

interface ConfiguredModelsGridProps {
  providers: AIModelProvider[];
  configuredModels: Record<string, ConfiguredModel[]>;
  capabilitySection: CapabilitySection;
  searchQuery: string;
  onEdit: (provider: AIModelProvider, capability: string, model: ConfiguredModel) => void;
  onSetDefault: (modelType: string, modelKey: string) => Promise<void>;
  onDelete: (modelType: string, modelKey: string, modelName: string) => void;
  isLoading?: boolean;
  /** When true (default), empty embedding shows the system built-in row. Disable on onboarding. */
  showEmbeddingBuiltinPlaceholder?: boolean;
}

export function ConfiguredModelsGrid({
  providers,
  configuredModels,
  capabilitySection,
  searchQuery,
  onEdit,
  onSetDefault,
  onDelete,
  isLoading = false,
  showEmbeddingBuiltinPlaceholder = true,
}: ConfiguredModelsGridProps) {
  const { t } = useTranslation();
  const rows = useMemo(() => {
    const types = modelTypesForSection(capabilitySection);
    const list: ConfiguredModel[] = [];
    for (const mt of types) {
      const arr = configuredModels[mt] ?? [];
      for (const m of arr) {
        list.push({ ...m, modelType: m.modelType || mt });
      }
    }

    if (
      showEmbeddingBuiltinPlaceholder &&
      capabilitySection === 'embedding' &&
      (configuredModels.embedding ?? []).length === 0
    ) {
      list.push(EMBEDDING_BUILTIN_PLACEHOLDER);
    }

    if (!searchQuery.trim()) return list;
    const q = searchQuery.toLowerCase();
    return list.filter((m) => {
      const p = providers.find((x) => x.providerId === m.provider);
      const name =
        m.modelFriendlyName ||
        (m.configuration?.model as string) ||
        m.provider ||
        '';
      const capLabel = capabilityLabelForModelType(m.modelType, t).toLowerCase();
      return (
        name.toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q) ||
        (p?.name.toLowerCase().includes(q) ?? false) ||
        capLabel.includes(q)
      );
    });
  }, [
    configuredModels,
    capabilitySection,
    searchQuery,
    providers,
    t,
    showEmbeddingBuiltinPlaceholder,
  ]);

  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ width: '100%', paddingTop: 80 }}>
        <Text size="2" style={{ color: 'var(--gray-9)' }}>
          {t('workspace.aiModels.loadingModels')}
        </Text>
      </Flex>
    );
  }

  if (rows.length === 0) {
    return (
      <Flex
        direction="column"
        align="center"
        justify="center"
        gap="2"
        style={{ width: '100%', paddingTop: 80 }}
      >
        <MaterialIcon name="tune" size={48} color="var(--gray-9)" />
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          {t('workspace.aiModels.emptyConfiguredCategory')}
        </Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      {rows.map((model) => (
        <ConfiguredModelRow
          key={`${model.modelType}-${model.modelKey}`}
          model={model}
          provider={providers.find((p) => p.providerId === model.provider)}
          onEdit={onEdit}
          onSetDefault={onSetDefault}
          onDelete={onDelete}
        />
      ))}
    </Flex>
  );
}

function ConfiguredModelRow({
  model,
  provider,
  onEdit,
  onSetDefault,
  onDelete,
}: {
  model: ConfiguredModel;
  provider: AIModelProvider | undefined;
  onEdit: (provider: AIModelProvider, capability: string, model: ConfiguredModel) => void;
  onSetDefault: (modelType: string, modelKey: string) => Promise<void>;
  onDelete: (modelType: string, modelKey: string, modelName: string) => void;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState(false);
  const [settingDefault, setSettingDefault] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const modelName =
    model.modelFriendlyName ||
    (model.configuration?.model as string) ||
    model.provider;
  const capReg = registryCapabilityForModelType(model.modelType);
  const capChip = capabilityLabelForModelType(model.modelType, t);
  const mt = model.modelType;
  const isBuiltinPlaceholder = isEmbeddingBuiltinPlaceholder(model);

  return (
    <Flex
      direction={{ initial: 'column', sm: 'row' }}
      align={{ initial: 'stretch', sm: 'center' }}
      justify={{ initial: 'start', sm: 'between' }}
      gap="4"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={modelRowCardStyle(hover)}
    >
      <Flex align="center" gap="4" style={{ minWidth: 0, flex: 1, width: '100%' }}>
        <Flex align="center" justify="center" style={MODEL_ROW_ICON_CONTAINER_STYLE}>
          {provider?.iconPath ? (
            <ThemeableAssetIcon
              src={provider.iconPath}
              size={28}
              color="var(--gray-12)"
              variant="flat"
            />
          ) : (
            <MaterialIcon name="smart_toy" size={24} color="var(--gray-9)" />
          )}
        </Flex>

        <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }} truncate>
            {modelName}
          </Text>
          <Flex align="center" gap="2" wrap="wrap">
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {provider?.name ?? model.provider}
            </Text>
            <Text
              size="1"
              style={{
                color: 'var(--gray-11)',
                backgroundColor: 'var(--gray-a3)',
                padding: '2px 8px',
                borderRadius: "2px",
                fontWeight: 500,
              }}
            >
              {capChip}
            </Text>
            {model.isDefault && (
              <Text
                size="1"
                style={{
                  color: 'var(--accent-11)',
                  backgroundColor: 'var(--accent-3)',
                  padding: '2px 8px',
                  borderRadius: "2px",
                  fontWeight: 600,
                }}
              >
                {t('workspace.aiModels.chipDefault')}
              </Text>
            )}
          </Flex>
        </Flex>
      </Flex>

      {!isBuiltinPlaceholder ? (
        <Popover.Root open={popoverOpen} onOpenChange={setPopoverOpen}>
          <Popover.Trigger>
            <button
              type="button"
              title={t('workspace.aiModels.moreOptions')}
              onClick={(e) => e.stopPropagation()}
              style={{
                appearance: 'none',
                margin: 0,
                padding: 6,
                border: 'none',
                outline: 'none',
                background: popoverOpen ? 'var(--gray-a3)' : 'transparent',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: 'var(--radius-2)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="more_vert" size={18} color="var(--gray-11)" />
            </button>
          </Popover.Trigger>
          <Popover.Content
            side="bottom"
            align="end"
            style={{ padding: 4, minWidth: 160 }}
          >
            <Flex direction="column">
              <PopoverMenuItem
                icon="edit"
                label={t('workspace.aiModels.actionEdit')}
                disabled={!provider || settingDefault}
                onClick={() => {
                  setPopoverOpen(false);
                  if (provider) onEdit(provider, capReg, model);
                }}
              />
              {!model.isDefault && (
                <PopoverMenuItem
                  icon="star_outline"
                  label={t('workspace.aiModels.actionSetDefault')}
                  loading={settingDefault}
                  onClick={async () => {
                    setSettingDefault(true);
                    try {
                      await onSetDefault(mt, model.modelKey);
                    } finally {
                      setSettingDefault(false);
                      setPopoverOpen(false);
                    }
                  }}
                />
              )}
              <PopoverMenuItem
                icon="delete"
                label={t('workspace.aiModels.actionDelete')}
                color="var(--red-9)"
                disabled={!provider || settingDefault}
                onClick={() => {
                  setPopoverOpen(false);
                  onDelete(mt, model.modelKey, modelName);
                }}
              />
            </Flex>
          </Popover.Content>
        </Popover.Root>
      ) : null}
    </Flex>
  );
}

function PopoverMenuItem({
  icon,
  label,
  onClick,
  color,
  disabled,
  loading,
}: {
  icon: string;
  label: string;
  onClick: () => void | Promise<void>;  
  color?: string;
  disabled?: boolean;
  loading?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      type="button"
      disabled={disabled || loading}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        appearance: 'none',
        border: 'none',
        outline: 'none',
        background: hovered && !disabled && !loading ? 'var(--gray-a3)' : 'transparent',
        cursor: disabled || loading ? 'default' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '7px 10px',
        borderRadius: 'var(--radius-2)',
        width: '100%',
        opacity: disabled ? 0.4 : 1,
      }}
    >
      {loading ? (
        <Spinner size={16} color={color ?? 'var(--gray-11)'} />
      ) : (
        <MaterialIcon name={icon} size={16} color={color ?? 'var(--gray-11)'} />
      )}
      <Text size="2" style={{ color: color ?? 'var(--gray-12)' }}>
        {label}
      </Text>
    </button>
  );
}
