'use client';

import React, { useMemo } from 'react';
import { Box, Button, Flex, RadioGroup, Select, Switch, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import {
  MODEL_SIZES,
  MODEL_THINKING_MODES,
  type DiscoveredModel,
  type ModelSize,
  type ModelThinking,
  type PickedModel,
} from '../types';

export type PerModelFlag = 'isReasoning' | 'isMultimodal';

export interface PerModelFlagField {
  name: PerModelFlag;
  label: string;
  description?: string;
}

export interface PickedDefaults {
  isReasoning: boolean;
  isMultimodal: boolean;
  contextLength: number | null;
}

/**
 * Prefilled context length stops here even when a model accepts more: bigger
 * windows cost more per call and rarely help retrieval. Typing a larger value still works.
 */
export const MAX_DEFAULT_CONTEXT_LENGTH = 300_000;

/** Provider metadata wins. A flag the provider did not report falls back to the form's value. */
export function pickedFromDiscovered(model: DiscoveredModel, defaults: PickedDefaults): PickedModel {
  return {
    id: model.id,
    modelFriendlyName: model.displayName !== model.id ? model.displayName : undefined,
    isMultimodal: model.isMultimodal ?? defaults.isMultimodal,
    isReasoning: model.isReasoning ?? defaults.isReasoning,
    contextLength: model.contextLength
      ? Math.min(model.contextLength, MAX_DEFAULT_CONTEXT_LENGTH)
      : defaults.contextLength,
  };
}

export function customPicked(id: string, defaults: PickedDefaults): PickedModel {
  return {
    id,
    isMultimodal: defaults.isMultimodal,
    isReasoning: defaults.isReasoning,
    contextLength: defaults.contextLength,
  };
}

interface PickedModelsListProps {
  picked: PickedModel[];
  models: DiscoveredModel[];
  onChange: (next: PickedModel[]) => void;
  /** Only the first model of a type may be made default here. */
  showDefault: boolean;
  defaultModelId: string | null;
  onDefaultChange: (modelId: string) => void;
  flagFields: PerModelFlagField[];
  contextLengthLabel?: string;
  /** Show the Size / Thinking selects Auto mode routes on (LLMs only). */
  showTraits?: boolean;
  disabled?: boolean;
}

/** Per-model settings, shown once more than one model is selected. */
export function PickedModelsList({
  picked,
  models,
  onChange,
  showDefault,
  defaultModelId,
  onDefaultChange,
  flagFields,
  contextLengthLabel,
  showTraits = false,
  disabled = false,
}: PickedModelsListProps) {
  const { t } = useTranslation();
  const discoveredById = useMemo(() => new Map(models.map((model) => [model.id, model])), [models]);

  const updatePicked = (id: string, patch: Partial<PickedModel>) => {
    onChange(picked.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  };

  return (
    <Flex direction="column" gap="2" data-testid="ai-picked-models">
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.aiModels.modelPickerSelected', { count: picked.length })}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          {t('workspace.aiModels.modelPickerSelectedHint')}
        </Text>
      </Flex>
      <RadioGroup.Root
        value={defaultModelId ?? undefined}
        onValueChange={onDefaultChange}
        disabled={disabled}
      >
        <Flex direction="column" gap="2">
          {picked.map((item) => (
            <PickedModelRow
              key={item.id}
              item={item}
              discovered={discoveredById.get(item.id)}
              flagFields={flagFields}
              contextLengthLabel={contextLengthLabel}
              showTraits={showTraits}
              showDefault={showDefault}
              disabled={disabled}
              onChange={(patch) => updatePicked(item.id, patch)}
              onRemove={() => onChange(picked.filter((entry) => entry.id !== item.id))}
            />
          ))}
        </Flex>
      </RadioGroup.Root>
    </Flex>
  );
}

function PickedModelRow({
  item,
  discovered,
  flagFields,
  contextLengthLabel,
  showTraits,
  showDefault,
  disabled,
  onChange,
  onRemove,
}: {
  item: PickedModel;
  discovered?: DiscoveredModel;
  flagFields: PerModelFlagField[];
  contextLengthLabel?: string;
  showTraits: boolean;
  showDefault: boolean;
  disabled: boolean;
  onChange: (patch: Partial<PickedModel>) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const placeholder = discovered?.displayName || item.id;
  return (
    <Box
      style={{
        border: '1px solid var(--gray-a5)',
        borderRadius: 'var(--radius-2)',
        padding: 10,
        backgroundColor: 'var(--color-surface)',
      }}
    >
      <Flex direction="column" gap="2">
        <Flex align="center" justify="between" gap="3">
          <Text size="2" weight="medium" truncate style={{ minWidth: 0 }}>
            {item.id}
          </Text>
          <Flex align="center" gap="3" style={{ flexShrink: 0 }}>
            {showDefault ? (
              <Text as="label" size="1">
                <Flex align="center" gap="1">
                  <RadioGroup.Item
                    value={item.id}
                    aria-label={t('workspace.aiModels.modelPickerDefaultFor', { model: item.id })}
                  />
                  {t('workspace.aiModels.modelPickerDefault')}
                </Flex>
              </Text>
            ) : null}
            <Button
              type="button"
              size="1"
              variant="ghost"
              color="gray"
              disabled={disabled}
              aria-label={t('workspace.aiModels.modelPickerRemoveFor', { model: item.id })}
              onClick={onRemove}
            >
              {t('workspace.aiModels.batchRemove')}
            </Button>
          </Flex>
        </Flex>
        <Flex gap="2" wrap="wrap">
          <TextField.Root
            value={item.modelFriendlyName ?? ''}
            placeholder={placeholder}
            aria-label={t('workspace.aiModels.modelPickerFriendlyName', { model: item.id })}
            disabled={disabled}
            style={{ flex: '2 1 180px' }}
            onChange={(event) => onChange({ modelFriendlyName: event.target.value })}
          />
          {contextLengthLabel ? (
            <TextField.Root
              type="number"
              min={1}
              value={item.contextLength ?? ''}
              placeholder={contextLengthLabel}
              aria-label={t('workspace.aiModels.modelPickerFieldFor', {
                field: contextLengthLabel,
                model: item.id,
              })}
              disabled={disabled}
              style={{ flex: '1 1 120px' }}
              onChange={(event) => {
                const raw = event.target.value.trim();
                const parsed = Number(raw);
                onChange({
                  contextLength: raw === '' || !Number.isFinite(parsed) || parsed <= 0 ? null : Math.floor(parsed),
                });
              }}
            />
          ) : null}
        </Flex>
        {showTraits ? (
          <Flex gap="2" wrap="wrap">
            <TraitSelect
              label={t('workspace.aiModels.traitSizeLabel')}
              model={item.id}
              options={MODEL_SIZES}
              optionLabel={(value) => t(`workspace.aiModels.traitSize.${value}`)}
              value={item.size}
              detected={discovered?.traits?.size ?? undefined}
              disabled={disabled}
              onChange={(size) => onChange({ size })}
            />
            <TraitSelect
              label={t('workspace.aiModels.traitThinkingLabel')}
              model={item.id}
              options={MODEL_THINKING_MODES}
              optionLabel={(value) => t(`workspace.aiModels.traitThinking.${value}`)}
              value={item.thinking}
              detected={discovered?.traits?.thinking ?? undefined}
              disabled={disabled}
              onChange={(thinking) => onChange({ thinking })}
            />
          </Flex>
        ) : null}
        {flagFields.length > 0 ? (
          <Flex gap="4" wrap="wrap">
            {flagFields.map((field) => (
              <Text as="label" size="2" key={field.name} title={field.description}>
                <Flex align="center" gap="2">
                  <Switch
                    size="1"
                    checked={item[field.name]}
                    disabled={disabled}
                    aria-label={t('workspace.aiModels.modelPickerFieldFor', {
                      field: field.label,
                      model: item.id,
                    })}
                    onCheckedChange={(checked) => onChange({ [field.name]: checked } as Partial<PickedModel>)}
                  />
                  {field.label}
                </Flex>
              </Text>
            ))}
          </Flex>
        ) : null}
      </Flex>
    </Box>
  );
}

const AUTO_DETECT = 'auto';

/**
 * Shows the admin's override, else the detected value. Picking the detected
 * value (or Auto-detect) clears the override so detection stays authoritative.
 */
function TraitSelect<T extends ModelSize | ModelThinking>({
  label,
  model,
  options,
  optionLabel,
  value,
  detected,
  disabled,
  onChange,
}: {
  label: string;
  model: string;
  options: readonly T[];
  optionLabel: (value: T) => string;
  value: T | undefined;
  detected: T | undefined;
  disabled: boolean;
  onChange: (value: T | undefined) => void;
}) {
  const { t } = useTranslation();
  const shown = value ?? detected ?? AUTO_DETECT;
  return (
    <Flex direction="column" gap="1" style={{ flex: '1 1 140px' }}>
      <Text size="1" style={{ color: 'var(--gray-11)' }}>
        {label}
        {value === undefined && detected ? ` · ${t('workspace.aiModels.traitDetected')}` : ''}
      </Text>
      <Select.Root
        value={shown}
        disabled={disabled}
        onValueChange={(next) =>
          onChange(next === AUTO_DETECT || next === detected ? undefined : (next as T))
        }
      >
        <Select.Trigger aria-label={t('workspace.aiModels.modelPickerFieldFor', { field: label, model })} />
        <Select.Content>
          <Select.Item value={AUTO_DETECT}>{t('workspace.aiModels.traitAutoDetect')}</Select.Item>
          {options.map((option) => (
            <Select.Item key={option} value={option}>
              {optionLabel(option)}
            </Select.Item>
          ))}
        </Select.Content>
      </Select.Root>
    </Flex>
  );
}
