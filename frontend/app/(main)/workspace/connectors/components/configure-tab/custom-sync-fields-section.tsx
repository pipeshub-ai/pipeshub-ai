'use client';

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SchemaFormField } from '../schema-form-field';
import type { ConnectorConfig, LastSyncSummary, SyncCustomField, SyncCustomFieldGroup } from '../../types';
import { isNonEditableSyncFieldLocked } from '../../utils/sync-custom-field-lock';
import { isSyncCustomFieldShown } from '../../utils/sync-custom-fields-validation';
import { connectorTypeKey, localizeSyncField, localizeSyncFieldGroup } from '../../utils/localize-sync-field';
import { getConnectorUiExtras, pageLimitStatus } from '../../utils/connector-presets';
import { SettingsSection } from './settings-section';
import { PresetChips } from './preset-chips';

type Translate = (key: string, options?: Record<string, unknown>) => string;

const asText = (v: unknown) => (v == null ? '' : Array.isArray(v) ? v.join('\n') : String(v));

/** "Respect robots.txt off · Largest file to download: 20 MB" for the fields that differ from their defaults. */
export function summarizeChangedFields(
  fields: SyncCustomField[],
  values: Record<string, unknown>,
  t: Translate
): string {
  const parts: string[] = [];
  for (const field of fields) {
    const value = values[field.name] ?? field.defaultValue;
    if (asText(value) === asText(field.defaultValue)) continue;
    const label = field.displayName;
    if (field.fieldType === 'BOOLEAN' || field.fieldType === 'CHECKBOX') {
      const on = value === true || value === 'true';
      parts.push(t(`workspace.connectors.fieldGroups.${on ? 'summaryOn' : 'summaryOff'}`, { label }));
      continue;
    }
    let shown: string;
    if (field.fieldType === 'NUMBER') {
      shown = [t('workspace.connectors.fieldGroups.number', { value: Number(value) }), field.unit]
        .filter(Boolean)
        .join(' ');
    } else if (Array.isArray(value)) {
      shown = value.join(', ');
    } else {
      shown = field.optionLabels?.[asText(value)] ?? asText(value);
    }
    parts.push(t('workspace.connectors.fieldGroups.summaryValue', { label, value: shown }));
  }
  return parts.join(' · ');
}

function PageLimitLine({ summary, limit }: { summary?: LastSyncSummary | null; limit: unknown }) {
  const { t } = useTranslation();
  const status = pageLimitStatus(summary, limit);
  if (!status) return null;
  const parts = [t('workspace.connectors.configTab.lastRunLimit.saved', { count: status.saved })];
  if (status.stoppedAtLimit) parts.push(t('workspace.connectors.configTab.lastRunLimit.stoppedAtLimit'));
  if (status.leftOut > 0) parts.push(t('workspace.connectors.configTab.lastRunLimit.leftOut', { count: status.leftOut }));
  return (
    <Flex direction="column" gap="1">
      <Text size="1" style={{ color: 'var(--gray-11)' }}>
        {parts.join(' · ')}
      </Text>
      {status.nearLimit ? (
        <Flex align="center" gap="1">
          <MaterialIcon name="warning" size={14} color="var(--amber-11)" />
          <Text size="1" style={{ color: 'var(--amber-11)' }}>
            {t('workspace.connectors.configTab.lastRunLimit.nearLimit')}
          </Text>
        </Flex>
      ) : null}
    </Flex>
  );
}

// ========================================
// CustomSyncFieldsSection
// ========================================

export function CustomSyncFieldsSection({
  fields,
  groups = [],
  values,
  errors,
  onChange,
  connectorConfig,
  connectorType,
  lastSyncSummary,
  isNewConnector = false,
  readOnly = false,
}: {
  fields: SyncCustomField[];
  /** Grouped fields render first, one section per group in this order. */
  groups?: SyncCustomFieldGroup[];
  values: Record<string, unknown>;
  errors: Record<string, string>;
  onChange: (key: string, value: unknown) => void;
  connectorConfig: ConnectorConfig | null;
  connectorType?: string;
  lastSyncSummary?: LastSyncSummary | null;
  isNewConnector?: boolean;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const typeKey = connectorTypeKey(connectorType);
  const [presetKeyEdited, setPresetKeyEdited] = useState(false);

  const localized = useMemo(() => fields.map((f) => localizeSyncField(f, typeKey, t)), [fields, typeKey, t]);
  const defaults = useMemo(() => Object.fromEntries(fields.map((f) => [f.name, f.defaultValue])), [fields]);
  const extras = getConnectorUiExtras(typeKey);
  // Presets only set fields this schema has.
  const presets = useMemo(
    () =>
      (extras?.presets ?? []).map((p) => ({
        ...p,
        values: Object.fromEntries(Object.entries(p.values).filter(([k]) => k in defaults)),
      })),
    [extras, defaults]
  );

  const shown = localized.filter((f) => isSyncCustomFieldShown(f, values, localized));
  const groupNames = new Set(groups.map((g) => g.name));
  const ungrouped = shown.filter((f) => !f.group || !groupNames.has(f.group));

  const handleChange = (key: string, value: unknown) => {
    if (presets.some((p) => key in p.values)) setPresetKeyEdited(true);
    onChange(key, value);
  };
  const applyPreset = (presetValues: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(presetValues)) onChange(key, value);
  };

  const renderField = (field: SyncCustomField) => {
    const locked = isNonEditableSyncFieldLocked(field, connectorConfig);
    const input = (
      <SchemaFormField
        key={field.name}
        field={field}
        value={values[field.name]}
        onChange={handleChange}
        error={errors[field.name]}
        disabled={locked || readOnly}
        disabledTooltip={
          readOnly
            ? t('workspace.connectors.configTab.localFsDesktopOnlySaveTooltip')
            : locked
            ? field.fieldType === 'URL'
              ? t('workspace.connectors.configTab.nonEditableUrlFieldTooltip')
              : t('workspace.connectors.configTab.nonEditableLockedFieldTooltip')
            : undefined
        }
      />
    );
    if (extras?.pageLimitField !== field.name) return input;
    return (
      <Flex key={field.name} direction="column" gap="1">
        {input}
        <PageLimitLine summary={lastSyncSummary} limit={values[field.name] ?? field.defaultValue} />
      </Flex>
    );
  };

  return (
    <>
      {groups.map((rawGroup) => {
        const groupFields = shown.filter((f) => f.group === rawGroup.name);
        const withPresets = presets.length > 0 && extras?.presetGroup === rawGroup.name;
        if (groupFields.length === 0 && !withPresets) return null;
        const group = localizeSyncFieldGroup(rawGroup, typeKey, t);
        return (
          <SettingsSection
            key={group.name}
            title={group.displayName}
            description={group.description}
            collapsible={Boolean(group.collapsed)}
            summary={group.collapsed ? summarizeChangedFields(groupFields, values, t) : undefined}
            forceOpen={groupFields.some((f) => Boolean(errors[f.name]))}
          >
            {groupFields.map(renderField)}
            {withPresets ? (
              <PresetChips
                typeKey={typeKey}
                presets={presets}
                values={values}
                defaults={defaults}
                suggestedId={isNewConnector ? extras?.suggestPreset?.(values) : null}
                confirmWhenCustom={presetKeyEdited || !isNewConnector}
                disabled={readOnly}
                onApply={applyPreset}
              />
            ) : null}
          </SettingsSection>
        );
      })}

      {ungrouped.length > 0 && (
        <Flex
          direction="column"
          gap="4"
          style={{
            padding: 16,
            backgroundColor: 'var(--olive-2)',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--olive-3)',
          }}
        >
          <Flex direction="column" gap="1">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.connectors.configTab.additionalSettings')}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {t('workspace.connectors.configTab.additionalSettingsDescription')}
            </Text>
          </Flex>

          {ungrouped.map(renderField)}
        </Flex>
      )}
    </>
  );
}
