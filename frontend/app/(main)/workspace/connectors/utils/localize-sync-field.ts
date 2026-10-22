import type { SyncCustomField, SyncCustomFieldGroup } from '../types';

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** i18n segment for a connector type: "Web" → "web", "Google Drive" → "googledrive". */
export function connectorTypeKey(connectorType: string | null | undefined): string {
  return (connectorType ?? '').toLowerCase().replace(/\s+/g, '');
}

/**
 * Swaps schema text for `workspace.connectors.fields.<type>.<field>.*` translations
 * where they exist; connectors without keys keep their schema text.
 */
export function localizeSyncField(field: SyncCustomField, typeKey: string, t: Translate): SyncCustomField {
  if (!typeKey) return field;
  const base = `workspace.connectors.fields.${typeKey}.${field.name}`;
  const text = (leaf: string, fallback: string | undefined) =>
    t(`${base}.${leaf}`, { defaultValue: fallback ?? '' }) || undefined;
  const optionLabels = field.options
    ? Object.fromEntries(
        field.options.map((opt) => [opt, text(`options.${opt}`, field.optionLabels?.[opt] ?? opt) ?? opt])
      )
    : field.optionLabels;
  return {
    ...field,
    displayName: text('label', field.displayName) ?? field.displayName,
    description: text('help', field.description),
    unit: text('unit', field.unit),
    optionLabels,
  };
}

export function localizeSyncFieldGroup(
  group: SyncCustomFieldGroup,
  typeKey: string,
  t: Translate
): SyncCustomFieldGroup {
  if (!typeKey) return group;
  const base = `workspace.connectors.fieldGroups.${typeKey}.${group.name}`;
  return {
    ...group,
    displayName: t(`${base}.title`, { defaultValue: group.displayName }) || group.displayName,
    description: t(`${base}.description`, { defaultValue: group.description ?? '' }) || undefined,
  };
}
