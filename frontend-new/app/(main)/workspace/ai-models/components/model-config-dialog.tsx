'use client';

import type { TFunction } from 'i18next';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Button, Callout, Flex, Switch, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { SchemaField } from '@/app/(main)/workspace/connectors/types';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { aiModelsCapabilityLabel } from '../capability-i18n';
import type { AIModelProvider, AIModelProviderField, ConfiguredModel } from '../types';
import { CAPABILITY_TO_MODEL_TYPE } from '../types';
import { AIModelsApi } from '../api';

const COMPAT_FIELD_NAMES = ['isReasoning', 'isMultimodal'] as const;

const READONLY_ROW_STYLE: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  minHeight: 32,
  paddingLeft: 10,
  paddingRight: 10,
  backgroundColor: 'var(--color-surface)',
  border: '1px solid var(--gray-a5)',
  borderRadius: 'var(--radius-2)',
  fontSize: 14,
  color: 'var(--gray-12)',
};

const CARD_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  border: '1px solid var(--gray-a5)',
  borderRadius: 'var(--radius-3)',
  padding: 16,
};

function isFieldValueSatisfied(field: AIModelProviderField, value: unknown): boolean {
  if (!field.required) return true;
  const t = field.fieldType;
  if (t === 'BOOLEAN' || t === 'CHECKBOX') return true;
  if (t === 'NUMBER') {
    if (value === '' || value === undefined || value === null) return false;
    const n = Number(value);
    return !Number.isNaN(n);
  }
  if (t === 'SELECT') {
    return String(value ?? '').trim() !== '';
  }
  return String(value ?? '').trim() !== '';
}

function allRequiredFieldsValid(fields: AIModelProviderField[], values: Record<string, unknown>): boolean {
  return fields.every((f) => isFieldValueSatisfied(f, values[f.name]));
}

function compatIcon(fieldName: string): string {
  if (fieldName === 'isReasoning') return 'lightbulb';
  if (fieldName === 'isMultimodal') return 'image';
  return 'tune';
}

function fieldStartAdornment(fieldName: string): React.ReactNode | undefined {
  if (fieldName === 'apiKey') {
    return <MaterialIcon name="key" size={16} color="var(--gray-10)" />;
  }
  if (fieldName === 'model' || fieldName === 'contextLength') {
    return <MaterialIcon name="widgets" size={16} color="var(--gray-10)" />;
  }
  return undefined;
}

interface ModelConfigDialogProps {
  open: boolean;
  mode: 'add' | 'edit';
  provider: AIModelProvider | null;
  capability: string | null;
  editModel: ConfiguredModel | null;
  /**
   * Number of models already configured for the target model type. Used in
   * `add` mode to decide whether the newly-added model should be auto-promoted
   * to default. Only the very first model of a type is auto-defaulted; for
   * every subsequent add the user must explicitly click "Set as default".
   * Defaults to `0` so callers that omit it (e.g. onboarding flows, which are
   * always first-time setup) preserve the first-model-auto-defaults behavior.
   */
  existingModelsCount?: number;
  onClose: () => void;
  onSaved: () => void;
}

export function ModelConfigDialog({
  open,
  mode,
  provider,
  capability,
  editModel,
  existingModelsCount = 0,
  onClose,
  onSaved,
}: ModelConfigDialogProps) {
  const { t } = useTranslation();
  const [fields, setFields] = useState<AIModelProviderField[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !provider || !capability) {
      setFields([]);
      setValues({});
      setError(null);
      return;
    }

    const capFields = provider.fields[capability] ?? [];
    setFields(capFields as AIModelProviderField[]);

    if (mode === 'edit' && editModel) {
      const initial: Record<string, unknown> = {};
      for (const f of capFields) {
        const field = f as AIModelProviderField;
        if (field.name in (editModel.configuration ?? {})) {
          initial[field.name] = (editModel.configuration as Record<string, unknown>)[field.name];
        } else if (field.name === 'isMultimodal') {
          initial[field.name] = editModel.isMultimodal ?? field.defaultValue;
        } else if (field.name === 'isReasoning') {
          initial[field.name] = editModel.isReasoning ?? field.defaultValue;
        } else if (field.name === 'contextLength') {
          initial[field.name] = editModel.contextLength ?? field.defaultValue;
        } else if (field.name === 'modelFriendlyName') {
          initial[field.name] = editModel.modelFriendlyName ?? '';
        } else {
          initial[field.name] = field.defaultValue ?? '';
        }
      }
      setValues(initial);
    } else {
      const defaults: Record<string, unknown> = {};
      for (const f of capFields) {
        const field = f as AIModelProviderField;
        defaults[field.name] = field.defaultValue ?? '';
      }
      setValues(defaults);
    }
  }, [open, provider, capability, mode, editModel]);

  const handleFieldChange = useCallback((name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleSave = async () => {
    if (!provider || !capability) return;
    setError(null);
    setSaving(true);

    try {
      const modelTypeFromCapability = CAPABILITY_TO_MODEL_TYPE[capability];
      const modelType =
        mode === 'edit' && editModel
          ? editModel.modelType
          : modelTypeFromCapability;

      if (!modelType) {
        throw new Error(`Unknown capability: ${capability}`);
      }

      const topLevelKeys = ['isMultimodal', 'isReasoning', 'contextLength'];
      const configuration: Record<string, unknown> = {};
      const topLevel: Record<string, unknown> = {};

      for (const [key, val] of Object.entries(values)) {
        if (topLevelKeys.includes(key)) {
          topLevel[key] = val;
        } else {
          configuration[key] = val;
        }
      }

      if (mode === 'add') {
        // Auto-default only the very first model of a given type. For every
        // subsequent add, leave `isDefault: false` so the user's current
        // default stays in place until they explicitly click "Set as default".
        // This is especially important for embeddings: silently changing the
        // default to a model with a different dimension / identity would
        // corrupt the existing vector collection. The backend's
        // set-default endpoint runs a health check to prevent that, so the
        // only way to actually switch the default is via the explicit button.
        const shouldAutoDefault = existingModelsCount === 0;
        await AIModelsApi.addProvider({
          modelType,
          provider: provider.providerId,
          configuration,
          isMultimodal: (topLevel.isMultimodal as boolean) ?? false,
          isReasoning: (topLevel.isReasoning as boolean) ?? false,
          isDefault: shouldAutoDefault,
          contextLength: topLevel.contextLength ? Number(topLevel.contextLength) : null,
        });
      } else if (editModel) {
        // Preserve the existing default flag on edit so that tweaking
        // dimensions / api key / etc. does not silently remove the model
        // from being the default.
        await AIModelsApi.updateProvider(modelType, editModel.modelKey, {
          provider: provider.providerId,
          configuration,
          isMultimodal: (topLevel.isMultimodal as boolean) ?? false,
          isReasoning: (topLevel.isReasoning as boolean) ?? false,
          isDefault: editModel.isDefault ?? false,
          contextLength: topLevel.contextLength ? Number(topLevel.contextLength) : null,
        });
      }

      onSaved();
      onClose();
    } catch (err: unknown) {
      const e = err as { response?: { data?: { error?: { message?: string }; message?: string } }; message?: string };
      const msg =
        e?.response?.data?.error?.message ??
        e?.response?.data?.message ??
        e?.message ??
        t('workspace.aiModels.configSaveErrorFallback');
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const capLabel = capability ? aiModelsCapabilityLabel(t, capability) : '';

  const headerTitle = useMemo(() => {
    const name = provider?.name ?? '';
    if (mode === 'add') {
      return t('workspace.aiModels.configPanelTitleAdd', { provider: name, capability: capLabel }).trim();
    }
    return t('workspace.aiModels.configPanelTitleEdit', { provider: name, capability: capLabel }).trim();
  }, [mode, provider?.name, capLabel, t]);

  const headerIcon = useMemo(() => {
    if (!provider?.iconPath) return undefined;
    return (
      <span style={{ display: 'inline-flex', borderRadius: 4, overflow: 'hidden', lineHeight: 0 }}>
        <ThemeableAssetIcon
          src={provider.iconPath}
          size={22}
          color="var(--gray-12)"
          variant="flat"
        />
      </span>
    );
  }, [provider?.iconPath]);

  const formValid = useMemo(
    () => (fields.length === 0 ? true : allRequiredFieldsValid(fields, values)),
    [fields, values]
  );

  const primaryBlocked = saving || !provider || !capability || !formValid;

  const headerActions = useMemo(
    () => (
      <Button
        type="button"
        variant="ghost"
        color="gray"
        size="2"
        aria-label={t('workspace.aiModels.configDocsOpenLabel')}
        style={{ cursor: 'pointer', gap: 6 }}
        onClick={() =>
          window.open(`${EXTERNAL_LINKS.documentation}ai-models/overview`, '_blank', 'noopener,noreferrer')
        }
      >
        <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
      </Button>
    ),
    [t]
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={headerTitle}
      icon={headerIcon}
      headerActions={headerActions}
      primaryLabel={mode === 'add' ? t('workspace.aiModels.configAddModel') : t('workspace.aiModels.configUpdateModel')}
      secondaryLabel={t('workspace.aiModels.cancel')}
      primaryLoading={saving}
      primaryDisabled={primaryBlocked}
      primaryTooltip={
        !formValid && !saving && Boolean(provider) && Boolean(capability)
          ? t('workspace.aiModels.configFillRequiredTooltip')
          : undefined
      }
      onPrimaryClick={() => {
        void handleSave();
      }}
      onSecondaryClick={onClose}
    >
      <ModelConfigFormBody
        provider={provider}
        capability={capability}
        capLabel={capLabel}
        fields={fields}
        values={values}
        saving={saving}
        error={error}
        onFieldChange={handleFieldChange}
      />
    </WorkspaceRightPanel>
  );
}

function resolveConfigInfoMessage(
  t: TFunction,
  capability: string | null,
  providerName: string,
  capLabel: string
): string {
  if (!capability) {
    return t('workspace.aiModels.configInfoNoCapability', { providerName });
  }
  if (capability === 'text_generation') {
    return t('workspace.aiModels.configInfoTextGeneration', { providerName });
  }
  if (capability === 'embedding') {
    return t('workspace.aiModels.configInfoEmbedding', { providerName });
  }
  if (capability === 'image_generation') {
    return t('workspace.aiModels.configInfoImageGeneration', { providerName });
  }
  return t('workspace.aiModels.configInfoFallback', { capLabel, providerName });
}

function ModelConfigFormBody({
  provider,
  capability,
  capLabel,
  fields,
  values,
  saving,
  error,
  onFieldChange,
}: {
  provider: AIModelProvider | null;
  capability: string | null;
  capLabel: string;
  fields: AIModelProviderField[];
  values: Record<string, unknown>;
  saving: boolean;
  error: string | null;
  onFieldChange: (name: string, value: unknown) => void;
}) {
  const { t } = useTranslation();
  const instanceField = useMemo(
    () => fields.find((f) => f.name === 'modelFriendlyName') ?? null,
    [fields]
  );

  const compatFields = useMemo(() => {
    return COMPAT_FIELD_NAMES.map((name) => fields.find((f) => f.name === name)).filter(
      (f): f is AIModelProviderField => Boolean(f)
    );
  }, [fields]);

  const modelConfigFields = useMemo(
    () =>
      fields.filter(
        (f) =>
          f.name !== 'modelFriendlyName' &&
          f.name !== 'isReasoning' &&
          f.name !== 'isMultimodal'
      ),
    [fields]
  );

  const section1Title = t('workspace.aiModels.configSectionCapabilityConfig', { capability: capLabel });

  if (fields.length === 0) {
    return (
      <Flex direction="column" gap="3">
        {provider?.notice ? (
          <Callout.Root color="amber" size="1" variant="surface">
            <Callout.Text>
              <Text size="2" style={{ color: 'var(--gray-12)' }}>
                {provider.notice}
              </Text>
            </Callout.Text>
          </Callout.Root>
        ) : null}
        <Text size="2" style={{ color: 'var(--gray-10)', padding: 8 }}>
          {t('workspace.aiModels.configNoConfigRequired')}
        </Text>
        {error && (
          <Text size="2" style={{ color: 'var(--red-11)', padding: '4px 0' }}>
            {error}
          </Text>
        )}
      </Flex>
    );
  }

  const providerName =
    provider?.name ?? t('workspace.aiModels.configInfoPlaceholderProvider');
  const infoMessage =
    provider && capability
      ? resolveConfigInfoMessage(t, capability, provider.name, capLabel)
      : resolveConfigInfoMessage(t, null, providerName, capLabel);

  const boolValue = (v: unknown) =>
    typeof v === 'boolean' ? v : v === 'true' ? true : v === 'false' ? false : Boolean(v);

  return (
    <Flex direction="column" gap="4">
      <Box style={CARD_STYLE}>
        <Flex direction="column" gap="3">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {section1Title}
          </Text>
          <Callout.Root color="green" size="1" variant="surface">
            <Callout.Text>
              <Text size="2" style={{ color: 'var(--gray-12)' }}>
                {infoMessage}
              </Text>
            </Callout.Text>
          </Callout.Root>
          {instanceField ? (
            <SchemaFormField
              field={toSchemaField(instanceField)}
              value={values[instanceField.name]}
              onChange={onFieldChange}
              disabled={saving}
              startAdornment={fieldStartAdornment(instanceField.name)}
            />
          ) : null}
        </Flex>
      </Box>

      <Box style={CARD_STYLE}>
        <Flex direction="column" gap="3">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.aiModels.configSectionModelConfiguration')}
          </Text>
          {provider ? (
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.aiModels.configProviderType')}
              </Text>
              <Box style={READONLY_ROW_STYLE}>
                <Text size="2">{t('workspace.aiModels.configProviderApiRow', { name: provider.name })}</Text>
              </Box>
            </Flex>
          ) : null}
          {modelConfigFields.map((field) => (
            <SchemaFormField
              key={field.name}
              field={toSchemaField(field)}
              value={values[field.name]}
              onChange={onFieldChange}
              disabled={saving}
              startAdornment={fieldStartAdornment(field.name)}
            />
          ))}
        </Flex>
      </Box>

      {compatFields.length > 0 ? (
        <Box style={CARD_STYLE}>
          <Flex direction="column" gap="3">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.aiModels.configSectionCompatibilities')}
            </Text>
            <Flex direction="column" gap="2">
              {compatFields.map((field) => (
                <Flex
                  key={field.name}
                  align="center"
                  justify="between"
                  gap="3"
                  style={{ minHeight: 44, paddingTop: 4, paddingBottom: 4 }}
                >
                  <Flex align="center" gap="3" style={{ minWidth: 0, flex: 1 }}>
                    <MaterialIcon name={compatIcon(field.name)} size={20} color="var(--gray-11)" />
                    <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                        {field.displayName}
                      </Text>
                      {field.description ? (
                        <Text size="1" style={{ color: 'var(--gray-10)' }}>
                          {field.description}
                        </Text>
                      ) : null}
                    </Flex>
                  </Flex>
                  <Switch
                    checked={boolValue(values[field.name])}
                    onCheckedChange={(checked) => onFieldChange(field.name, checked)}
                    disabled={saving}
                  />
                </Flex>
              ))}
            </Flex>
          </Flex>
        </Box>
      ) : null}

      {error && (
        <Text size="2" style={{ color: 'var(--red-11)', padding: '4px 0' }}>
          {error}
        </Text>
      )}
    </Flex>
  );
}

function toSchemaField(field: AIModelProviderField): SchemaField {
  return {
    name: field.name,
    displayName: field.displayName,
    fieldType: field.fieldType,
    required: field.required,
    defaultValue: field.defaultValue,
    placeholder: field.placeholder,
    description: field.description,
    isSecret: field.isSecret,
    options: field.options?.map((o) => ({ id: o.value, label: o.label })),
    validation: field.validation,
  } as unknown as SchemaField;
}
