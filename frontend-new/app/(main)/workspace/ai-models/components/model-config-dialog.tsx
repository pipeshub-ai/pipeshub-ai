'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { SchemaField } from '@/app/(main)/workspace/connectors/types';
import type { AIModelProvider, AIModelProviderField, ConfiguredModel } from '../types';
import { CAPABILITY_TO_MODEL_TYPE, CAPABILITY_DISPLAY_NAMES } from '../types';
import { AIModelsApi } from '../api';

interface ModelConfigDialogProps {
  open: boolean;
  mode: 'add' | 'edit';
  provider: AIModelProvider | null;
  capability: string | null;
  editModel: ConfiguredModel | null;
  onClose: () => void;
  onSaved: () => void;
}

export function ModelConfigDialog({
  open,
  mode,
  provider,
  capability,
  editModel,
  onClose,
  onSaved,
}: ModelConfigDialogProps) {
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
      const modelType = CAPABILITY_TO_MODEL_TYPE[capability];
      if (!modelType) throw new Error(`Unknown capability: ${capability}`);

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
        await AIModelsApi.addProvider({
          modelType,
          provider: provider.providerId,
          configuration,
          isMultimodal: (topLevel.isMultimodal as boolean) ?? false,
          isReasoning: (topLevel.isReasoning as boolean) ?? false,
          contextLength: topLevel.contextLength ? Number(topLevel.contextLength) : null,
        });
      } else if (editModel) {
        await AIModelsApi.updateProvider(modelType, editModel.modelKey, {
          provider: provider.providerId,
          configuration,
          isMultimodal: (topLevel.isMultimodal as boolean) ?? false,
          isReasoning: (topLevel.isReasoning as boolean) ?? false,
          contextLength: topLevel.contextLength ? Number(topLevel.contextLength) : null,
        });
      }

      onSaved();
      onClose();
    } catch (err: any) {
      const msg =
        err?.response?.data?.error?.message ??
        err?.response?.data?.message ??
        err?.message ??
        'Failed to save model configuration';
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const capLabel = capability ? (CAPABILITY_DISPLAY_NAMES[capability] ?? capability) : '';

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Content style={{ maxWidth: 520 }}>
        <Dialog.Title>
          {mode === 'add' ? 'Add' : 'Edit'} {provider?.name} {capLabel}
        </Dialog.Title>

        <Flex direction="column" gap="3" style={{ marginTop: 8 }}>
          {fields.length === 0 ? (
            <Text size="2" style={{ color: 'var(--gray-10)', padding: 16 }}>
              No configuration required for this provider.
            </Text>
          ) : (
            fields.map((field) => (
              <SchemaFormField
                key={field.name}
                field={toSchemaField(field)}
                value={values[field.name]}
                onChange={handleFieldChange}
                disabled={saving}
              />
            ))
          )}

          {error && (
            <Text size="2" style={{ color: 'var(--red-11)', padding: '4px 0' }}>
              {error}
            </Text>
          )}
        </Flex>

        <Flex gap="3" justify="end" style={{ marginTop: 16 }}>
          <Dialog.Close>
            <Button variant="soft" color="gray" disabled={saving} style={{ cursor: 'pointer' }}>
              Cancel
            </Button>
          </Dialog.Close>
          <Button onClick={handleSave} disabled={saving} style={{ cursor: 'pointer' }}>
            {saving ? 'Saving...' : mode === 'add' ? 'Add Model' : 'Update Model'}
          </Button>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

/**
 * Convert our registry field type to the SchemaField shape expected by SchemaFormField.
 */
function toSchemaField(field: AIModelProviderField): SchemaField {
  return {
    name: field.name,
    displayName: field.displayName,
    fieldType: field.fieldType as any,
    required: field.required,
    defaultValue: field.defaultValue,
    placeholder: field.placeholder,
    description: field.description,
    isSecret: field.isSecret,
    options: field.options,
    validation: field.validation as any,
  };
}
