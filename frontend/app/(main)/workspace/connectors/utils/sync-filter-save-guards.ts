/**
 * Legacy parity (see legacy connector-config-form): when saving filters/sync settings,
 * prompt before API save if manual indexing is on or no meaningful sync filters are set.
 */
import type { FilterSchemaField } from '../types';

type FilterRow = { operator?: string; value?: unknown };

function hasMeaningfulSyncFilterValue(
  field: { filterType?: string },
  f: FilterRow | undefined
): boolean {
  if (!f?.operator) return false;
  if (field.filterType === 'boolean') return true;
  if (Array.isArray(f.value)) return f.value.length > 0;
  if (
    field.filterType === 'datetime' &&
    f.value &&
    typeof f.value === 'object' &&
    !Array.isArray(f.value)
  ) {
    const d = f.value as { start?: string | number | null; end?: string | number | null };
    return (
      (d.start != null && d.start !== '') ||
      (d.end != null && d.end !== '')
    );
  }
  return f.value !== undefined && f.value !== null && f.value !== '';
}

/**
 * True when at least one sync-schema filter has a meaningful value (indexing filters excluded).
 */
export function hasAnySyncFiltersSelected(
  syncFields: FilterSchemaField[] | undefined,
  syncFormValues: Record<string, unknown>
): boolean {
  if (!syncFields?.length) return false;
  return syncFields.some((field) => {
    const row = syncFormValues[field.name] as FilterRow | undefined;
    return row ? hasMeaningfulSyncFilterValue(field, row) : false;
  });
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
