/**
 * Legacy parity (see legacy connector-config-form): when saving filters/sync settings,
 * prompt before API save if manual indexing is on or no meaningful sync filters are set.
 */
import type { FilterSchemaField } from '../types';

type FilterRow = { operator?: string; value?: unknown };

function isFilterRow(raw: unknown): raw is FilterRow {
  return (
    typeof raw === 'object' &&
    raw !== null &&
    !Array.isArray(raw) &&
    'operator' in raw &&
    typeof (raw as FilterRow).operator === 'string'
  );
}

function isListLikeFilterField(field: FilterSchemaField): boolean {
  if (field.fieldType === 'MULTISELECT' || field.fieldType === 'TAGS') return true;
  const ft = String(field.filterType ?? '').toLowerCase();
  return ft === 'list' || ft === 'multiselect';
}

/**
 * Whether a filter row counts as configured (UI active state, summary chips, save guards).
 * Must stay aligned with default seeding in filters-section.tsx.
 */
export function isMeaningfulFilterRow(field: FilterSchemaField, raw: unknown): boolean {
  if (raw === undefined || raw === null) return false;
  if (!isFilterRow(raw) || !raw.operator?.trim()) return false;
  const operator = raw.operator.trim();
  const { value } = raw;
  const ft = String(field.filterType ?? '').toLowerCase();
  if (ft === 'list' || ft === 'multiselect' || isListLikeFilterField(field)) {
    if (Array.isArray(value)) {
      if (value.length > 0 || field.defaultOperator) {
        return true;
      }
    }
    return false;
  }
  if (ft === 'boolean') {
    return value === true || value === false;
  }
  if (ft === 'datetime') {
    const op = operator.toLowerCase();
    if (op.startsWith('last_')) return true;
    const v = value as { start?: unknown; end?: unknown } | null;
    if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
    const startOk =
      v.start !== undefined &&
      v.start !== null &&
      v.start !== '' &&
      !(typeof v.start === 'number' && (v.start <= 0 || Number.isNaN(v.start)));
    const endOk =
      v.end !== undefined &&
      v.end !== null &&
      v.end !== '' &&
      !(typeof v.end === 'number' && (v.end <= 0 || Number.isNaN(v.end)));
    return startOk || endOk;
  }
  if (value === '' || value === undefined || value === null) return false;
  if (typeof value === 'string' && !value.trim()) return false;
  return true;
}

/**
 * True when at least one sync-schema filter has a meaningful value (indexing filters excluded).
 */
export function hasAnySyncFiltersSelected(
  syncFields: FilterSchemaField[] | undefined,
  syncFormValues: Record<string, unknown>
): boolean {
  if (!syncFields?.length) return false;
  return syncFields.some((field) => isMeaningfulFilterRow(field, syncFormValues[field.name]));
}

/** Manual indexing toggle lives under indexing filters as `enable_manual_sync`. */
export function isManualIndexingEnabled(indexingFormValues: Record<string, unknown>): boolean {
  const raw = indexingFormValues.enable_manual_sync as FilterRow | { value?: unknown } | undefined;
  if (raw === undefined || raw === null) return false;
  if (typeof raw === 'object' && 'value' in raw) {
    return raw.value === true || raw.value === 'true';
  }
  return false;
}
