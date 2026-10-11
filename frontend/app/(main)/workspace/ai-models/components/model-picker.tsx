'use client';

import React, { useMemo, useState } from 'react';
import { Badge, Box, Button, Checkbox, Flex, RadioGroup, Switch, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { DiscoveredModel, PickedModel } from '../types';

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

/** Provider metadata wins. A flag the provider did not report falls back to the form's value. */
export function pickedFromDiscovered(model: DiscoveredModel, defaults: PickedDefaults): PickedModel {
  return {
    id: model.id,
    modelFriendlyName: model.displayName !== model.id ? model.displayName : undefined,
    isMultimodal: model.isMultimodal ?? defaults.isMultimodal,
    isReasoning: model.isReasoning ?? defaults.isReasoning,
    contextLength: model.contextLength ?? defaults.contextLength,
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

/**
 * Model ids typed into the single-model field before the user started picking.
 * They are kept as selections so switching to the picker never drops input.
 */
export function seedTypedModels(
  next: PickedModel[],
  typed: string,
  defaults: PickedDefaults,
  friendlyName?: string,
): PickedModel[] {
  const ids = typed.split(',').map((id) => id.trim()).filter(Boolean);
  const missing = ids.filter((id) => !next.some((item) => item.id === id));
  const seeded = missing.map((id, index) => ({
    ...customPicked(id, defaults),
    ...(index === 0 && ids.length === 1 && friendlyName?.trim()
      ? { modelFriendlyName: friendlyName.trim() }
      : {}),
  }));
  return [...seeded, ...next];
}

interface ModelPickerProps {
  models: DiscoveredModel[];
  picked: PickedModel[];
  onChange: (next: PickedModel[]) => void;
  /** Only the first model of a type may be made default here. */
  showDefault: boolean;
  defaultModelId: string | null;
  onDefaultChange: (modelId: string) => void;
  flagFields: PerModelFlagField[];
  contextLengthLabel?: string;
  defaults: PickedDefaults;
  disabled?: boolean;
}

export function ModelPicker({
  models,
  picked,
  onChange,
  showDefault,
  defaultModelId,
  onDefaultChange,
  flagFields,
  contextLengthLabel,
  defaults,
  disabled = false,
}: ModelPickerProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [showOther, setShowOther] = useState(false);
  const [customId, setCustomId] = useState('');
  const selected = useMemo(() => new Set(picked.map((item) => item.id)), [picked]);
  const discoveredById = useMemo(() => new Map(models.map((model) => [model.id, model])), [models]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return models.filter((model) => {
      const otherOnly = model.capabilities.length === 1 && model.capabilities[0] === 'other';
      if (otherOnly && !showOther && !selected.has(model.id)) return false;
      if (!needle) return true;
      return (
        model.id.toLowerCase().includes(needle) ||
        model.displayName.toLowerCase().includes(needle)
      );
    });
  }, [models, query, showOther, selected]);

  const add = (item: PickedModel) => {
    onChange([...picked, item]);
  };

  const remove = (id: string) => {
    onChange(picked.filter((item) => item.id !== id));
  };

  const toggle = (model: DiscoveredModel) => {
    if (selected.has(model.id)) remove(model.id);
    else add(pickedFromDiscovered(model, defaults));
  };

  const addCustom = () => {
    const id = customId.trim();
    setCustomId('');
    if (!id || selected.has(id)) return;
    add(customPicked(id, defaults));
  };

  const updatePicked = (id: string, patch: Partial<PickedModel>) => {
    onChange(picked.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  };

  return (
    <Flex direction="column" gap="3" data-testid="ai-model-picker">
      {models.length > 0 ? (
        <Flex direction="column" gap="2">
          <TextField.Root
            value={query}
            placeholder={t('workspace.aiModels.modelPickerSearch')}
            onChange={(event) => setQuery(event.target.value)}
            disabled={disabled}
          />
          <Text as="label" size="2">
            <Flex align="center" gap="2">
              <Checkbox
                checked={showOther}
                aria-label={t('workspace.aiModels.modelPickerOther')}
                onCheckedChange={(value) => setShowOther(value === true)}
                disabled={disabled}
              />
              {t('workspace.aiModels.modelPickerOther')}
            </Flex>
          </Text>
          <Flex
            direction="column"
            gap="1"
            style={{ maxHeight: 280, overflowY: 'auto', paddingRight: 4 }}
          >
            {visible.length === 0 ? (
              <Text size="2" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.aiModels.modelPickerEmpty')}
              </Text>
            ) : (
              visible.map((model) => (
                <Flex
                  key={model.id}
                  align="center"
                  justify="between"
                  gap="3"
                  style={{
                    border: '1px solid var(--gray-a5)',
                    borderRadius: 'var(--radius-2)',
                    padding: '8px 10px',
                  }}
                >
                  <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                    <Checkbox
                      checked={selected.has(model.id)}
                      disabled={disabled}
                      aria-label={model.displayName || model.id}
                      onCheckedChange={() => toggle(model)}
                    />
                    <Flex direction="column" style={{ minWidth: 0 }}>
                      <Text size="2" weight="medium" truncate>
                        {model.displayName || model.id}
                      </Text>
                      {model.displayName && model.displayName !== model.id ? (
                        <Text size="1" style={{ color: 'var(--gray-10)' }} truncate>
                          {model.id}
                        </Text>
                      ) : null}
                    </Flex>
                  </Flex>
                  <ModelBadges model={model} />
                </Flex>
              ))
            )}
          </Flex>
        </Flex>
      ) : null}

      <Flex gap="2">
        <TextField.Root
          value={customId}
          placeholder={t('workspace.aiModels.modelPickerCustomPlaceholder')}
          aria-label={t('workspace.aiModels.modelPickerCustomLabel')}
          disabled={disabled}
          style={{ flex: 1 }}
          onChange={(event) => setCustomId(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              addCustom();
            }
          }}
        />
        <Button type="button" variant="soft" disabled={disabled || !customId.trim()} onClick={addCustom}>
          {t('workspace.aiModels.modelPickerAddCustom')}
        </Button>
      </Flex>

      {picked.length > 0 ? (
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
                  showDefault={showDefault}
                  disabled={disabled}
                  onChange={(patch) => updatePicked(item.id, patch)}
                  onRemove={() => remove(item.id)}
                />
              ))}
            </Flex>
          </RadioGroup.Root>
        </Flex>
      ) : null}
    </Flex>
  );
}

function ModelBadges({ model }: { model: DiscoveredModel }) {
  const { t } = useTranslation();
  return (
    <Flex gap="1" wrap="wrap" justify="end">
      {model.contextLength ? (
        <Badge variant="soft">{t('workspace.aiModels.badgeContext', { count: model.contextLength })}</Badge>
      ) : null}
      {model.isMultimodal ? <Badge variant="soft">{t('workspace.aiModels.badgeVision')}</Badge> : null}
      {model.supportsTools ? <Badge variant="soft">{t('workspace.aiModels.badgeTools')}</Badge> : null}
      {model.isReasoning ? <Badge variant="soft">{t('workspace.aiModels.badgeReasoning')}</Badge> : null}
      {model.deprecated ? (
        <Badge color="orange" variant="soft">{t('workspace.aiModels.badgeDeprecated')}</Badge>
      ) : null}
    </Flex>
  );
}

function PickedModelRow({
  item,
  discovered,
  flagFields,
  contextLengthLabel,
  showDefault,
  disabled,
  onChange,
  onRemove,
}: {
  item: PickedModel;
  discovered?: DiscoveredModel;
  flagFields: PerModelFlagField[];
  contextLengthLabel?: string;
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
