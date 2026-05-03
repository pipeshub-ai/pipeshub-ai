'use client';

import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  Flex,
  Text,
  Select,
  Button,
  Box,
  Badge,
  DropdownMenu,
  Switch,
  Tooltip,
  IconButton,
  Checkbox,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { FilterDropdown } from '@/app/components/ui/filter-dropdown';
import { DateRangePicker } from '@/app/components/ui/date-range-picker';
import type { DateFilterType } from '@/app/components/ui/date-range-picker';
import { useConnectorsStore } from '../../store';
import { ConnectorsApi } from '../../api';
import type { FilterSchemaField } from '../../types';
import { WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';

type FilterSection = 'sync' | 'indexing';

/** Legacy parity: shown in its own card above sync/indexing filter accordions (not in “Indexing filters”). */
const MANUAL_INDEXING_FIELD_NAME = 'enable_manual_sync';

const INITIAL_LIMIT = 20;
const PAGE_LIMIT = 20;
const MAX_OPTIONS_IN_MEMORY = 5000;

const inputLike: React.CSSProperties = {
  height: 32,
  width: '100%',
  padding: '6px 8px',
  borderRadius: 'var(--radius-2)',
  border: '1px solid var(--gray-a5)',
  fontSize: 14,
  boxSizing: 'border-box',
  backgroundColor: 'var(--color-surface)',
};

/** `datetime-local` expects `YYYY-MM-DDTHH:mm` in local time (legacy time-utils + MUI parity). */
function epochMsToDatetimeLocal(ms: number): string {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${mo}-${day}T${h}:${min}`;
}

function datetimeLocalToEpochMs(s: string): number | null {
  const t = new Date(s).getTime();
  if (!s?.trim() || Number.isNaN(t)) return null;
  return t;
}

function pieceToDatetimeLocal(v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  if (typeof v === 'number') {
    return v > 0 ? epochMsToDatetimeLocal(v) : '';
  }
  if (typeof v === 'string') {
    const trimmed = v.trim();
    if (!trimmed) return '';
    const asNum = Number(trimmed);
    if (!Number.isNaN(asNum) && asNum > 0 && String(asNum) === trimmed) {
      return epochMsToDatetimeLocal(asNum);
    }
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(trimmed)) {
      return trimmed.slice(0, 16);
    }
    const t = new Date(trimmed).getTime();
    return !Number.isNaN(t) && t > 0 ? epochMsToDatetimeLocal(t) : '';
  }
  return '';
}

/** Map saved filter value (epoch in `start`/`end`, etc.) to strings for `<input type="datetime-local">`. */
function datetimeFilterDisplayPair(operator: string, rawValue: unknown): { start: string; end: string } {
  const op = operator.toLowerCase();
  if (op.startsWith('last_')) {
    return { start: '', end: '' };
  }

  if (
    rawValue &&
    typeof rawValue === 'object' &&
    !Array.isArray(rawValue) &&
    'start' in rawValue &&
    'end' in rawValue
  ) {
    const r = rawValue as { start?: unknown; end?: unknown };
    return {
      start: pieceToDatetimeLocal(r.start),
      end: pieceToDatetimeLocal(r.end),
    };
  }

  if (typeof rawValue === 'number' && rawValue > 0) {
    const local = epochMsToDatetimeLocal(rawValue);
    if (op === 'is_before') return { start: '', end: local };
    if (op === 'is_after') return { start: local, end: '' };
    return { start: local, end: '' };
  }

  if (typeof rawValue === 'string' && rawValue.trim()) {
    const n = Number(rawValue.trim());
    if (!Number.isNaN(n) && n > 0 && String(n) === rawValue.trim()) {
      const local = epochMsToDatetimeLocal(n);
      if (op === 'is_before') return { start: '', end: local };
      if (op === 'is_after') return { start: local, end: '' };
      return { start: local, end: '' };
    }
  }

  return { start: '', end: '' };
}

/** Persist editor state in the same shape the connector API expects (epoch ms or null). */
function persistDatetimeRangeValue(
  operator: string,
  startLocal: string,
  endLocal: string
): { start: number | null; end: number | null } {
  const op = operator.toLowerCase();
  if (op.startsWith('last_')) {
    return { start: null, end: null };
  }
  const s = datetimeLocalToEpochMs(startLocal);
  const e = datetimeLocalToEpochMs(endLocal);
  if (op === 'is_after') {
    return { start: s, end: null };
  }
  if (op === 'is_before') {
    return { start: null, end: e };
  }
  return { start: s, end: e };
}

/** Re-map stored value when the user picks a different datetime operator (legacy parity). */
function coerceDatetimePersistedForOperator(operator: string, rawValue: unknown) {
  const { start, end } = datetimeFilterDisplayPair(operator, rawValue);
  return persistDatetimeRangeValue(operator, start, end);
}

function formatOperatorLabel(operator: string): string {
  return operator
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

/** Align connector datetime operators with shared `DateRangePicker` modes (users / workspace filters). */
function operatorToDateFilterType(operator: string): DateFilterType {
  const o = operator.toLowerCase();
  if (o === 'is_after') return 'after';
  if (o === 'is_before') return 'before';
  if (o === 'is_between') return 'between';
  if (o === 'on' || o === 'equals' || o === 'is') return 'on';
  return 'between';
}

interface FilterRowValue {
  operator: string;
  value: unknown;
  type?: string;
}

function isFilterRowValue(v: unknown): v is FilterRowValue {
  return (
    typeof v === 'object' &&
    v !== null &&
    'operator' in v &&
    typeof (v as FilterRowValue).operator === 'string'
  );
}

function booleanDefaultValue(field: FilterSchemaField): boolean {
  if (typeof field.defaultValue === 'boolean') return field.defaultValue;
  /** Schema omission must not imply ON — matches manual-indexing copy (OFF is default). */
  return false;
}

function defaultFilterOperator(field: FilterSchemaField): string {
  const fromSchema = field.defaultOperator ?? field.operators?.[0] ?? 'is';
  return String(fromSchema).trim() || 'is';
}

function getRow(field: FilterSchemaField, raw: unknown): FilterRowValue {
  if (isFilterRowValue(raw)) {
    const op = raw.operator?.trim()
      ? raw.operator
      : defaultFilterOperator(field);
    return {
      operator: op,
      value: raw.value,
      type: raw.type || field.filterType,
    };
  }
  return {
    operator: defaultFilterOperator(field),
    value:
      field.filterType === 'list'
        ? []
        : field.filterType === 'boolean'
          ? booleanDefaultValue(field)
          : field.filterType === 'datetime'
            ? { start: null, end: null }
            : '',
    type: field.filterType,
  };
}

/** Indexing filters: legacy UI kept any row with an operator visible (incl. empty lists / default booleans). */
function hasActiveFilterRow(raw: unknown): boolean {
  if (raw === undefined || raw === null) return false;
  if (!isFilterRowValue(raw)) return false;
  return Boolean(String(raw.operator || '').trim());
}

/** Whether saved form state counts as a configured filter (for summary chips). */
function isMeaningfulCommitted(field: FilterSchemaField, raw: unknown): boolean {
  if (raw === undefined || raw === null) return false;
  if (!isFilterRowValue(raw) || !raw.operator?.trim()) return false;
  const row = raw;
  const ft = String(field.filterType ?? '').toLowerCase();
  if (ft === 'list' || ft === 'multiselect' || isListLikeField(field)) {
    return Array.isArray(row.value) && row.value.length > 0;
  }
  if (ft === 'boolean') {
    return row.value === true || row.value === false;
  }
  if (ft === 'datetime') {
    const op = row.operator.toLowerCase();
    if (op.startsWith('last_')) return true;
    const v = row.value as { start?: unknown; end?: unknown } | null;
    if (!v || typeof v !== 'object') return false;
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
  if (row.value === '' || row.value === undefined || row.value === null) return false;
  if (typeof row.value === 'string' && !row.value.trim()) return false;
  return true;
}

function summarizeCommittedFilter(
  field: FilterSchemaField,
  row: FilterRowValue,
  section: FilterSection
): string {
  const op = formatOperatorLabel(row.operator);
  const ft = String(field.filterType ?? '').toLowerCase();
  if (ft === 'list' || ft === 'multiselect' || isListLikeField(field)) {
    const ids = listFilterIds(row.value);
    if (ids.length === 0) return op;
    const byId = listFilterLabelsById(row.value);
    const parts = ids.map((id) => byId[id] || id);
    const joined = parts.join(', ');
    const max = 80;
    const tail = joined.length <= max ? joined : `${joined.slice(0, max - 1)}…`;
    return `${op} · ${tail}`;
  }
  if (ft === 'datetime') {
    const opLower = row.operator.toLowerCase();
    if (opLower.startsWith('last_')) return `${op}`;
    const { start: ds, end: de } = datetimeFilterDisplayPair(row.operator, row.value);
    return ds || de ? `${op} · date range` : op;
  }
  if (ft === 'boolean') {
    const yn = row.value ? 'Yes' : 'No';
    if (section === 'indexing') return yn;
    return `${op} · ${yn}`;
  }
  const s = row.value === undefined || row.value === null ? '' : String(row.value);
  const short = s.length > 36 ? `${s.slice(0, 34)}…` : s;
  return short ? `${op} · ${short}` : op;
}

function isListLikeField(field: FilterSchemaField): boolean {
  if (field.fieldType === 'MULTISELECT' || field.fieldType === 'TAGS') return true;
  const ft = String(field.filterType ?? '').toLowerCase();
  return ft === 'list' || ft === 'multiselect';
}

/** API may return list values as string[] or { id, label }[] (Confluence / legacy). */
function listFilterIds(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const item of raw) {
    if (typeof item === 'string' && item.trim()) out.push(item);
    else if (item && typeof item === 'object' && 'id' in item) {
      const id = (item as { id?: unknown }).id;
      if (typeof id === 'string' && id) out.push(id);
    }
  }
  return out;
}

function listFilterLabelsById(raw: unknown): Record<string, string> {
  const map: Record<string, string> = {};
  if (!Array.isArray(raw)) return map;
  for (const item of raw) {
    if (typeof item === 'string' && item) {
      map[item] = item;
    } else if (item && typeof item === 'object' && 'id' in item) {
      const id = String((item as { id?: unknown }).id ?? '');
      if (!id) continue;
      const label = (item as { label?: unknown }).label;
      map[id] = typeof label === 'string' && label.trim() ? label : id;
    }
  }
  return map;
}

function listValueUsesObjectEntries(raw: unknown): boolean {
  return (
    Array.isArray(raw) &&
    raw.some((x) => x !== null && typeof x === 'object' && !Array.isArray(x) && 'id' in (x as object))
  );
}

function buildListPersistedValue(
  ids: string[],
  previous: unknown,
  optionList: { value: string; label: string }[]
): unknown {
  const optById = new Map(optionList.map((o) => [o.value, o.label]));
  const prevById = new Map<string, string>();
  if (Array.isArray(previous)) {
    for (const item of previous) {
      if (item && typeof item === 'object' && 'id' in item) {
        const id = String((item as { id: string }).id);
        const lab = (item as { label?: string }).label;
        prevById.set(id, typeof lab === 'string' && lab.trim() ? lab : id);
      } else if (typeof item === 'string' && item) {
        prevById.set(item, item);
      }
    }
  }
  const preferObjects =
    listValueUsesObjectEntries(previous) ||
    ids.some((id) => {
      const lab = prevById.get(id) ?? optById.get(id) ?? id;
      return lab !== id;
    });

  if (preferObjects) {
    return ids.map((id) => ({
      id,
      label: prevById.get(id) ?? optById.get(id) ?? id,
    }));
  }
  return ids;
}

function mergeOptionsWithSelected(
  base: { value: string; label: string }[],
  selectedIds: string[],
  labelsById: Record<string, string>
): { value: string; label: string }[] {
  const m = new Map(base.map((o) => [o.value, o]));
  for (const id of selectedIds) {
    if (!m.has(id)) {
      m.set(id, { value: id, label: labelsById[id] ?? id });
    }
  }
  return Array.from(m.values());
}

function dedupeAppend(
  base: { id: string; label: string }[],
  add: { id: string; label: string }[]
): { id: string; label: string }[] {
  const seen = new Set(base.map((x) => x.id));
  const out = [...base];
  for (const o of add) {
    if (!seen.has(o.id)) {
      seen.add(o.id);
      out.push(o);
    }
  }
  return out.slice(0, MAX_OPTIONS_IN_MEMORY);
}

// ========================================
// Multi-select (legacy filters parity)
// ========================================

function ConnectorFilterMultiSelect({
  field,
  value,
  onValueChange,
  connectorId,
  portalContainer,
}: {
  field: FilterSchemaField;
  value: unknown;
  onValueChange: (v: unknown) => void;
  connectorId: string | null;
  portalContainer: HTMLElement | null;
}) {
  const selectedIds = useMemo(() => listFilterIds(value), [value]);
  const labelsById = useMemo(() => listFilterLabelsById(value), [value]);
  const isDynamic = field.optionSourceType === 'dynamic';

  const [options, setOptions] = useState<{ id: string; label: string }[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  /** First page / search refetch — avoids empty list flashing “No results” (legacy autocomplete parity). */
  const [initialLoading, setInitialLoading] = useState(false);
  const fetchingRef = useRef(false);
  const cursorRef = useRef<string | undefined>(undefined);
  /** Last page fetched for page-based dynamic options (SharePoint, etc.); cursor APIs ignore updates here. */
  const pageRef = useRef(0);
  const searchRef = useRef('');

  const loadOptions = useCallback(
    async (append: boolean) => {
      if (!connectorId || !isDynamic || fetchingRef.current) return;
      fetchingRef.current = true;
      if (!append) {
        setInitialLoading(true);
        setOptions([]);
        setHasMore(false);
        pageRef.current = 0;
      }
      if (append) setLoadingMore(true);
      try {
        const search = searchRef.current.trim() || undefined;
        const prevCursorForAppend = append ? cursorRef.current : undefined;

        const params =
          !append
            ? {
                page: 1,
                limit: INITIAL_LIMIT,
                ...(search ? { search } : {}),
              }
            : prevCursorForAppend
              ? {
                  limit: PAGE_LIMIT,
                  cursor: prevCursorForAppend,
                  ...(search ? { search } : {}),
                }
              : {
                  page: pageRef.current + 1,
                  limit: PAGE_LIMIT,
                  ...(search ? { search } : {}),
                };

        const res = await ConnectorsApi.getFilterFieldOptions(connectorId, field.name, params);

        const rows = res.options ?? [];
        cursorRef.current = res.cursor;

        setOptions((prev) => {
          const merged = append ? dedupeAppend(prev, rows) : dedupeAppend([], rows);
          const noNewRows = append && merged.length === prev.length;
          const canLoadMore = Boolean(
            res.hasMore && merged.length < MAX_OPTIONS_IN_MEMORY && !noNewRows
          );
          setHasMore(canLoadMore);
          return merged;
        });

        if (!append) {
          pageRef.current = res.page ?? 1;
        } else if (!prevCursorForAppend) {
          pageRef.current = res.page ?? pageRef.current + 1;
        }
      } catch {
        if (!append) {
          setOptions([]);
          setHasMore(false);
        }
      } finally {
        fetchingRef.current = false;
        setLoadingMore(false);
        if (!append) setInitialLoading(false);
      }
    },
    [connectorId, field.name, isDynamic]
  );

  const handlePopoverOpen = useCallback(
    (open: boolean) => {
      if (!open || !isDynamic || !connectorId) return;
      searchRef.current = '';
      cursorRef.current = undefined;
      void loadOptions(false);
    },
    [isDynamic, connectorId, loadOptions]
  );

  const handleSearch = useCallback(
    (q: string) => {
      if (!isDynamic || !connectorId) return;
      searchRef.current = q;
      cursorRef.current = undefined;
      void loadOptions(false);
    },
    [isDynamic, connectorId, loadOptions]
  );

  const handleLoadMore = useCallback(() => {
    if (!isDynamic || !hasMore || loadingMore || !connectorId) return;
    void loadOptions(true);
  }, [isDynamic, hasMore, loadingMore, connectorId, loadOptions]);

  const plural = `${field.displayName} values`;

  if (isDynamic && !connectorId) {
    return (
      <Text size="1" color="gray">
        Complete authentication and save the connector to load options for this filter.
      </Text>
    );
  }

  const staticOpts = useMemo(
    () => staticOptionsList(field).map((o) => ({ value: o.id, label: o.label })),
    [field]
  );
  const mergedStaticOpts = useMemo(
    () => mergeOptionsWithSelected(staticOpts, selectedIds, labelsById),
    [staticOpts, selectedIds, labelsById]
  );

  const selectLabel = `Select ${field.displayName}`;

  if (!isDynamic) {
    return (
      <FilterDropdown
        label={selectLabel}
        triggerTitle={selectLabel}
        icon="filter_list"
        pluralLabel={plural}
        options={mergedStaticOpts}
        selectedValues={selectedIds}
        onSelectionChange={(next) => onValueChange(buildListPersistedValue(next, value, mergedStaticOpts))}
        searchable
        triggerLayout="simple"
        summaryBelowTrigger
        portalContainer={portalContainer}
        popoverContentStyle={{ minWidth: 280, maxWidth: 400, zIndex: 10001 }}
      />
    );
  }

  const dynamicOpts = useMemo(() => options.map((o) => ({ value: o.id, label: o.label })), [options]);
  const mergedDynamicOpts = useMemo(
    () => mergeOptionsWithSelected(dynamicOpts, selectedIds, labelsById),
    [dynamicOpts, selectedIds, labelsById]
  );

  return (
    <FilterDropdown
      label={selectLabel}
      triggerTitle={selectLabel}
      icon="filter_list"
      pluralLabel={plural}
      options={mergedDynamicOpts}
      selectedValues={selectedIds}
      onSelectionChange={(next) => onValueChange(buildListPersistedValue(next, value, mergedDynamicOpts))}
      searchable
      triggerLayout="simple"
      summaryBelowTrigger
      onSearch={handleSearch}
      onLoadMore={handleLoadMore}
      isLoadingMore={loadingMore}
      hasMore={hasMore}
      isLoadingOptions={initialLoading}
      onPopoverOpenChange={handlePopoverOpen}
      portalContainer={portalContainer}
      popoverContentStyle={{ minWidth: 280, maxWidth: 420, zIndex: 10001 }}
    />
  );
}

// ========================================
// Manual indexing (legacy UI: own section, not under “Indexing filters”)
// ========================================

function ManualIndexingSection({ field }: { field: FilterSchemaField }) {
  const { formData, setFilterFormValue } = useConnectorsStore();
  const raw = formData.filters.indexing[field.name];

  useEffect(() => {
    if (raw !== undefined && raw !== null) return;
    const initial = getRow(field, undefined);
    setFilterFormValue('indexing', field.name, {
      operator: initial.operator,
      value: initial.value,
      type: field.filterType || initial.type,
    });
  }, [field, raw, setFilterFormValue]);

  const row = getRow(field, raw);
  const checked = row.value === true || row.value === 'true';

  // Tooltip body is a <p>; only phrasing content is valid (no div/Flex inside).
  const tooltipContent = (
    <Text
      as="span"
      size="1"
      style={{
        display: 'block',
        maxWidth: 300,
        whiteSpace: 'pre-line',
        color: 'var(--gray-12)',
        lineHeight: 1.55,
      }}
    >
      {`OFF (default): Records are automatically indexed based on the indexing filters below.

ON: No records are automatically indexed. You manually choose which records to index from the knowledge base.`}
    </Text>
  );

  return (
    <Box
      style={{
        padding: 16,
        backgroundColor: 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex align="start" justify="between" gap="4" wrap="wrap">
        <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap="2" wrap="wrap">
            <Box
              style={{
                padding: 6,
                borderRadius: 'var(--radius-2)',
                background: 'var(--green-3)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="tune" size={16} color="var(--green-11)" />
            </Box>
            <Flex align="center" gap="1" wrap="wrap" style={{ minWidth: 0 }}>
              <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
                {field.displayName}
              </Text>
              <Tooltip content={tooltipContent}>
                <IconButton
                  type="button"
                  size="1"
                  variant="ghost"
                  color="gray"
                  aria-label="About manual indexing"
                  style={{ cursor: 'help', flexShrink: 0 }}
                >
                  <MaterialIcon name="info" size={16} color="var(--gray-10)" />
                </IconButton>
              </Tooltip>
            </Flex>
          </Flex>
          {field.description ? (
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              {field.description}
            </Text>
          ) : null}
        </Flex>
        <Switch
          color="jade"
          size="2"
          checked={checked}
          onCheckedChange={(next) => {
            setFilterFormValue('indexing', field.name, {
              operator: defaultFilterOperator(field),
              value: next,
              type: field.filterType ?? 'boolean',
            });
          }}
        />
      </Flex>
    </Box>
  );
}

// ========================================
// FiltersSection
// ========================================

export function FiltersSection() {
  const { connectorSchema, panelConnectorId, formData, setFilterFormValue } = useConnectorsStore();

  const syncFields = useMemo(
    () => connectorSchema?.filters?.sync?.schema?.fields ?? [],
    [connectorSchema]
  );
  const indexingFieldsAll = useMemo(
    () => connectorSchema?.filters?.indexing?.schema?.fields ?? [],
    [connectorSchema]
  );
  const manualIndexingField = useMemo(
    () => indexingFieldsAll.find((f) => f.name === MANUAL_INDEXING_FIELD_NAME) ?? null,
    [indexingFieldsAll]
  );
  const indexingFields = useMemo(
    () => indexingFieldsAll.filter((f) => f.name !== MANUAL_INDEXING_FIELD_NAME),
    [indexingFieldsAll]
  );

  const [activeSync, setActiveSync] = useState<string[]>([]);
  const [activeIndexing, setActiveIndexing] = useState<string[]>([]);

  useEffect(() => {
    if (!panelConnectorId || !connectorSchema) {
      setActiveSync([]);
      setActiveIndexing([]);
      return;
    }
    const { formData: snapshotForm, setFilterFormValue: patchFilter } = useConnectorsStore.getState();

    // Legacy parity: every indexing schema field gets default operator/value in form state.
    // Configure save is not gated on dirty state; seeding only fills keys missing from merged config.
    for (const field of indexingFields) {
      const key = field.name;
      const existing = snapshotForm.filters.indexing[key];
      if (existing === undefined || existing === null) {
        const row = getRow(field, undefined);
        patchFilter('indexing', key, {
          operator: row.operator,
          value: row.value,
          type: field.filterType || row.type,
        });
      }
    }

    const { formData: fd } = useConnectorsStore.getState();
    const seedSync = (fields: FilterSchemaField[], vals: Record<string, unknown>) =>
      fields.filter((f) => isMeaningfulCommitted(f, vals[f.name])).map((f) => f.name);
    const seedIndexingActive = (fields: FilterSchemaField[], vals: Record<string, unknown>) =>
      fields.filter((f) => hasActiveFilterRow(vals[f.name])).map((f) => f.name);

    setActiveSync(seedSync(syncFields, fd.filters.sync));
    setActiveIndexing(seedIndexingActive(indexingFields, fd.filters.indexing));
  }, [panelConnectorId, connectorSchema, syncFields, indexingFields]);

  if (syncFields.length === 0 && indexingFields.length === 0 && !manualIndexingField) {
    return null;
  }

  return (
    <Flex direction="column" gap="5">
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          Indexing & sync filters
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          Indexing filters always apply—toggle booleans or adjust values as needed. For sync filters,
          add only what you need; list and date filters use an operator and value, and you can clear
          a sync filter when you do not want that constraint.
        </Text>
      </Flex>

      {manualIndexingField ? <ManualIndexingSection field={manualIndexingField} /> : null}

      {syncFields.length > 0 && (
        <FilterCategoryBlock
          title="Sync filters"
          section="sync"
          fields={syncFields}
          values={formData.filters.sync}
          activeFieldNames={activeSync}
          setActiveFieldNames={setActiveSync}
          connectorId={panelConnectorId}
          onChange={setFilterFormValue}
        />
      )}

      {indexingFields.length > 0 && (
        <FilterCategoryBlock
          title="Indexing filters"
          section="indexing"
          fields={indexingFields}
          values={formData.filters.indexing}
          activeFieldNames={activeIndexing}
          setActiveFieldNames={setActiveIndexing}
          connectorId={panelConnectorId}
          onChange={setFilterFormValue}
          showConfiguredPreview={false}
        />
      )}
    </Flex>
  );
}

function FilterCategoryBlock({
  title,
  section,
  fields,
  values,
  activeFieldNames,
  setActiveFieldNames,
  connectorId,
  onChange,
  showConfiguredPreview = true,
}: {
  title: string;
  section: FilterSection;
  fields: FilterSchemaField[];
  values: Record<string, unknown>;
  activeFieldNames: string[];
  setActiveFieldNames: React.Dispatch<React.SetStateAction<string[]>>;
  connectorId: string | null;
  onChange: (section: FilterSection, name: string, value: unknown) => void;
  /** Green summary chips; hidden for indexing filters (legacy + less clutter). */
  showConfiguredPreview?: boolean;
}) {
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  /** Indexing filters are always-on (legacy); sync filters stay add/remove. */
  const allowRemoveFilter = section === 'sync';
  const availableToAdd = allowRemoveFilter ? fields.filter((f) => !activeFieldNames.includes(f.name)) : [];

  const addField = (fieldName: string) => {
    const field = fields.find((f) => f.name === fieldName);
    if (!field) return;
    setActiveFieldNames((prev) => [...prev, fieldName]);
    const row = getRow(field, undefined);
    onChange(section, field.name, {
      operator: row.operator,
      value: row.value,
      type: field.filterType || row.type,
    });
  };

  const removeField = (fieldName: string) => {
    setActiveFieldNames((prev) => prev.filter((n) => n !== fieldName));
    onChange(section, fieldName, undefined);
  };

  type FilterPreviewItem =
    | {
        name: string;
        kind: 'list';
        fieldLabel: string;
        operatorLabel: string;
        valueLabels: string[];
      }
    | { name: string; kind: 'single'; chipLine: string };

  const configuredPreviewItems = useMemo((): FilterPreviewItem[] => {
    return activeFieldNames
      .map((name) => {
        const field = fields.find((f) => f.name === name);
        if (!field) return null;
        const raw = values[name];
        if (!isMeaningfulCommitted(field, raw)) return null;
        const row = getRow(field, raw);
        if (isListLikeField(field)) {
          const ids = listFilterIds(row.value);
          const byId = listFilterLabelsById(row.value);
          const valueLabels = ids.map((id) => byId[id] || id);
          return {
            name,
            kind: 'list' as const,
            fieldLabel: field.displayName,
            operatorLabel: formatOperatorLabel(row.operator),
            valueLabels,
          };
        }
        return {
          name,
          kind: 'single' as const,
          chipLine: `${field.displayName}: ${summarizeCommittedFilter(field, row, section)}`,
        };
      })
      .filter((x): x is FilterPreviewItem => x !== null);
  }, [activeFieldNames, fields, section, values]);

  return (
    <Box
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        padding: 16,
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <Flex align="center" justify="between" gap="3" wrap="wrap" style={{ marginBottom: 14 }}>
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {title}
        </Text>
        {availableToAdd.length > 0 ? (
          <DropdownMenu.Root>
            <DropdownMenu.Trigger>
              <Button type="button" size="1" variant="soft" color="green" style={{ cursor: 'pointer' }}>
                <MaterialIcon name="add" size={14} color="var(--green-11)" />
                Add filter
              </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content
              size="1"
              variant="soft"
              style={{ zIndex: 10001, minWidth: 220 }}
              container={panelBodyPortal ?? undefined}
            >
              {availableToAdd.map((f) => (
                <DropdownMenu.Item key={f.name} onSelect={() => addField(f.name)} style={{ cursor: 'pointer' }}>
                  {f.displayName}
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        ) : null}
      </Flex>

      {showConfiguredPreview ? (
        configuredPreviewItems.length > 0 ? (
          <Flex direction="column" gap="3" style={{ marginBottom: 14 }}>
            {configuredPreviewItems.map((item) =>
              item.kind === 'list' ? (
                <Box key={item.name} style={{ width: '100%', minWidth: 0 }}>
                  <Text
                    size="1"
                    weight="medium"
                    style={{ color: 'var(--gray-11)', display: 'block', marginBottom: 8, lineHeight: 1.4 }}
                  >
                    {item.fieldLabel} · {item.operatorLabel}
                  </Text>
                  <Flex wrap="wrap" gap="2" align="start">
                    {item.valueLabels.map((text, idx) => (
                      <Badge
                        key={`${item.name}-${idx}`}
                        size="1"
                        color="green"
                        variant="soft"
                        radius="full"
                        title={text}
                        style={{
                          whiteSpace: 'normal',
                          wordBreak: 'break-word',
                          maxWidth: '100%',
                          height: 'auto',
                          textAlign: 'left',
                          lineHeight: 1.35,
                          padding: '6px 10px',
                        }}
                      >
                        {text}
                      </Badge>
                    ))}
                  </Flex>
                </Box>
              ) : (
                <Badge
                  key={item.name}
                  size="1"
                  color="green"
                  variant="soft"
                  radius="full"
                  title={item.chipLine}
                  style={{
                    width: 'fit-content',
                    maxWidth: '100%',
                    whiteSpace: 'normal',
                    wordBreak: 'break-word',
                    height: 'auto',
                    textAlign: 'left',
                    lineHeight: 1.35,
                    padding: '6px 10px',
                  }}
                >
                  {item.chipLine}
                </Badge>
              )
            )}
          </Flex>
        ) : activeFieldNames.length > 0 ? (
          <Text size="1" color="gray" style={{ marginBottom: 14 }}>
            Finish operator and value for each filter below to see a summary here.
          </Text>
        ) : (
          <Text size="1" color="gray" style={{ marginBottom: 14 }}>
            No filters yet. Use &quot;Add filter&quot; to choose one of the supported filters.
          </Text>
        )
      ) : null}

      <Flex direction="column" gap="4">
        {activeFieldNames.length === 0 ? null : (
          <>
            {activeFieldNames.map((name) => {
              const field = fields.find((f) => f.name === name);
              if (!field) return null;
              return (
                <FilterFieldRow
                  key={name}
                  field={field}
                  section={section}
                  row={getRow(field, values[field.name])}
                  connectorId={connectorId}
                  onChange={onChange}
                  onClear={() => removeField(field.name)}
                  allowClear={allowRemoveFilter}
                />
              );
            })}
          </>
        )}
      </Flex>
    </Box>
  );
}

function FilterFieldRow({
  field,
  section,
  row,
  connectorId,
  onChange,
  onClear,
  allowClear = true,
}: {
  field: FilterSchemaField;
  section: FilterSection;
  row: FilterRowValue;
  connectorId: string | null;
  onChange: (section: FilterSection, name: string, value: unknown) => void;
  onClear: () => void;
  allowClear?: boolean;
}) {
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const operators = useMemo(() => {
    const fromSchema = field.operators?.filter((o) => o && o.trim()) ?? [];
    const current = row.operator?.trim() ?? '';
    if (fromSchema.length > 0) {
      // Saved config may use operators not listed in schema (e.g. API `equals` vs schema `is` / `is_not`).
      if (current && !fromSchema.includes(current)) {
        return [current, ...fromSchema];
      }
      return fromSchema;
    }
    if (current) return [current];
    if (field.defaultOperator?.trim()) return [field.defaultOperator];
    return [] as string[];
  }, [field.operators, field.defaultOperator, row.operator]);

  const listLike = isListLikeField(field);
  const isBooleanField = field.filterType === 'boolean';

  const commit = (next: FilterRowValue) => {
    const trimmedOp = next.operator?.trim();
    if (!isBooleanField && !trimmedOp) {
      if (allowClear) {
        onClear();
        return;
      }
    }
    const op = trimmedOp || defaultFilterOperator(field);
    onChange(section, field.name, {
      operator: op,
      value: next.value,
      type: field.filterType || next.type,
    });
  };

  const cardShell: React.CSSProperties = {
    padding: 14,
    borderRadius: 'var(--radius-2)',
    border: '1px solid var(--olive-3)',
    backgroundColor: 'var(--color-surface)',
    width: '100%',
    boxSizing: 'border-box',
  };

  if (isBooleanField) {
    const checked = row.value === true || row.value === 'true';
    return (
      <Box style={cardShell}>
        <Flex align="start" gap="3" style={{ width: '100%' }}>
          <Checkbox
            checked={checked}
            onCheckedChange={(v) =>
              commit({
                ...row,
                operator: defaultFilterOperator(field),
                value: v === true,
              })
            }
            style={{ flexShrink: 0, cursor: 'pointer', marginTop: 2 }}
          />
          <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {field.displayName}
            </Text>
            {field.description ? (
              <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
                {field.description}
              </Text>
            ) : null}
          </Flex>
        </Flex>
      </Box>
    );
  }

  return (
    <Box style={cardShell}>
      <Flex direction="column" gap="3">
        <Flex align="center" justify="between" gap="2" wrap="wrap">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {field.displayName}
          </Text>
          {allowClear ? (
            <Button type="button" size="1" variant="ghost" color="gray" onClick={onClear} style={{ cursor: 'pointer' }}>
              <MaterialIcon name="close" size={14} color="var(--gray-11)" />
              Clear
            </Button>
          ) : null}
        </Flex>
        {field.description ? (
          <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
            {field.description}
          </Text>
        ) : null}

        <Box
          style={{
            display: 'flex',
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: '12px 16px',
            width: '100%',
            alignItems: 'flex-start',
          }}
        >
          <Box
            style={{
              flex: '0 1 220px',
              minWidth: 0,
              maxWidth: '100%',
              width: 220,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <Text size="1" weight="medium" style={{ color: 'var(--gray-11)' }}>
              Operator
            </Text>
            {operators.length > 0 ? (
              <Select.Root
                value={row.operator || undefined}
                onValueChange={(nextOperator) => {
                  if (field.filterType === 'datetime') {
                    commit({
                      ...row,
                      operator: nextOperator,
                      value: coerceDatetimePersistedForOperator(nextOperator, row.value),
                    });
                    return;
                  }
                  commit({ ...row, operator: nextOperator });
                }}
              >
                <Select.Trigger placeholder="Choose operator…" style={{ width: '100%', height: 32 }} />
                <Select.Content
                  position="popper"
                  style={{ zIndex: 10000 }}
                  container={panelBodyPortal ?? undefined}
                >
                  {operators.map((op) => (
                    <Select.Item key={op} value={op}>
                      {formatOperatorLabel(op)}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            ) : (
              <input
                type="text"
                value={row.operator}
                onChange={(e) => commit({ ...row, operator: e.target.value })}
                placeholder="Operator"
                style={inputLike}
              />
            )}
          </Box>

          <Box
            style={{
              flex: '1 1 280px',
              minWidth: 0,
              maxWidth: '100%',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <Text size="1" weight="medium" style={{ color: 'var(--gray-11)' }}>
              {listLike
                ? field.displayName
                : field.filterType === 'datetime'
                  ? field.displayName
                  : 'Value'}
            </Text>
            {listLike ? (
              <ConnectorFilterMultiSelect
                field={field}
                value={row.value}
                onValueChange={(v) => commit({ ...row, value: v })}
                connectorId={connectorId}
                portalContainer={panelBodyPortal}
              />
            ) : (
              <FilterValueEditor
                field={field}
                operator={row.operator}
                value={row.value}
                onValueChange={(value) => commit({ ...row, value })}
                portalContainer={panelBodyPortal}
              />
            )}
          </Box>
        </Box>
      </Flex>
    </Box>
  );
}

function FilterValueEditor({
  field,
  operator,
  value,
  onValueChange,
  portalContainer,
}: {
  field: FilterSchemaField;
  operator: string;
  value: unknown;
  onValueChange: (v: unknown) => void;
  portalContainer?: HTMLElement | null;
}) {
  const ft = field.filterType;

  if (ft === 'boolean') {
    const checked = value === true || value === 'true';
    return (
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
        <input type="checkbox" checked={checked} onChange={(e) => onValueChange(e.target.checked)} />
        <Text size="2">Yes</Text>
      </label>
    );
  }

  if (ft === 'number') {
    return (
      <FormField label="Value">
        <input
          type="number"
          value={value === undefined || value === null || value === '' ? '' : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') {
              onValueChange(null);
              return;
            }
            const n = parseFloat(raw);
            onValueChange(Number.isNaN(n) ? null : n);
          }}
          style={inputLike}
        />
      </FormField>
    );
  }

  if (ft === 'datetime') {
    const opLower = operator.toLowerCase();
    if (opLower.startsWith('last_')) {
      return (
        <Text size="2" color="gray" style={{ lineHeight: 1.55 }}>
          This operator uses a rolling window — no fixed dates to set.
        </Text>
      );
    }

    const { start, end } = datetimeFilterDisplayPair(operator, value);
    const pickerType = operatorToDateFilterType(operator);
    const startDateProp = pickerType === 'before' ? undefined : start || undefined;
    const endDateProp =
      pickerType === 'before'
        ? end || undefined
        : pickerType === 'between'
          ? end || undefined
          : undefined;

    return (
      <Box style={{ width: '100%', minWidth: 0 }}>
        <DateRangePicker
          label={`Select ${field.displayName}`}
          icon="schedule"
          startDate={startDateProp}
          endDate={endDateProp}
          dateType={pickerType}
          fixedDateType={pickerType}
          withTime
          triggerVariant="field"
          summaryBelowTrigger
          portalContainer={portalContainer ?? null}
          defaultDateType={pickerType}
          onApply={(startDate, endDate, dateType) => {
            let startLocal = '';
            let endLocal = '';
            if (dateType === 'after' || dateType === 'on') {
              startLocal = startDate ?? '';
            } else if (dateType === 'before') {
              endLocal = (endDate ?? startDate) ?? '';
            } else if (dateType === 'between') {
              startLocal = startDate ?? '';
              endLocal = endDate ?? '';
            }
            onValueChange(persistDatetimeRangeValue(operator, startLocal, endLocal));
          }}
          onClear={() => {
            onValueChange(persistDatetimeRangeValue(operator, '', ''));
          }}
        />
      </Box>
    );
  }

  return (
    <FormField label="Value">
      <input
        type="text"
        value={value === undefined || value === null ? '' : String(value)}
        onChange={(e) => onValueChange(e.target.value)}
        style={inputLike}
      />
    </FormField>
  );
}

function staticOptionsList(field: FilterSchemaField): { id: string; label: string }[] {
  if (!field.options?.length) return [];
  return field.options.map((o) =>
    typeof o === 'string' ? { id: o, label: o } : { id: o.id, label: o.label }
  );
}
