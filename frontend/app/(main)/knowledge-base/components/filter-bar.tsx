'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon, FilterDropdown, DateRangePicker, type DateFilterType } from '@/app/components/ui';
import { useIsMobile, useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { useKnowledgeBaseStore } from '../store';
import { useTranslation } from 'react-i18next';
import type {
  PageViewMode,
  SizeRange,
  IndexingStatus,
  NodeOrigin,
  RecordType,
  KnowledgeBaseFilter,
  AllRecordsFilter,
} from '../types';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import { LapTimerIcon } from '@/app/components/ui/lap-timer-icon';
import { KbSortMenu } from './kb-sort-menu';

interface KBFilterBarProps {
  pageViewMode: PageViewMode;
}

type StagedFilter = KnowledgeBaseFilter | AllRecordsFilter;

function listKey(values?: readonly string[]): string {
  return [...(values ?? [])].sort().join('\u0002');
}

// Empty and omitted values compare equal, and selection order does not.
function managedFilterKey(filter: StagedFilter): string {
  return [
    listKey(filter.recordTypes),
    listKey(filter.indexingStatus),
    listKey(filter.origins),
    listKey(filter.connectorIds),
    filter.sizeRange ?? '',
    filter.createdAfter ?? '',
    filter.createdBefore ?? '',
    filter.createdDateType ?? '',
    filter.updatedAfter ?? '',
    filter.updatedBefore ?? '',
    filter.updatedDateType ?? '',
  ].join('\u0001');
}

function appliedFilterKey(filter: StagedFilter): string {
  return [
    managedFilterKey(filter),
    listKey(filter.nodeTypes),
    filter.searchQuery ?? '',
  ].join('\u0001');
}

function filterHasValues(filter: StagedFilter): boolean {
  return managedFilterKey(filter).replace(/\u0001/g, '') !== '';
}

export function FilterBar({ pageViewMode }: KBFilterBarProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  // Below desktop the chips scroll in one row instead of wrapping a lone chip.
  const scrollRow = useIsBelowDesktop();
  const {
    // Collections mode state and actions
    filter,
    setFilter,
    clearFilter,
    // All Records mode state and actions
    allRecordsFilter,
    setAllRecordsFilter,
    clearAllRecordsFilter,
    // Table data for available filters
    tableData,
    allRecordsTableData,
    // Sort (shared with the sortable column headers)
    sort,
    setSort,
    allRecordsSort,
    setAllRecordsSort,
  } = useKnowledgeBaseStore();

  const isCollectionsMode = pageViewMode === 'collections';

  // Applied filter (what the table is showing). Edits stay in `draft` until Apply.
  const activeFilter = isCollectionsMode ? filter : allRecordsFilter;
  const appliedKey = `${isCollectionsMode ? 'collections' : 'all-records'}:${appliedFilterKey(activeFilter)}`;
  const activeFilterRef = useRef(activeFilter);
  activeFilterRef.current = activeFilter;
  const [draft, setDraft] = useState<StagedFilter>(activeFilter);

  // URL hydration, sidebar selection, mode switch, and Clear replace the draft.
  useEffect(() => {
    setDraft(activeFilterRef.current);
  }, [appliedKey]);

  const updateFilter = (patch: Partial<KnowledgeBaseFilter> | Partial<AllRecordsFilter>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };

  const applyFilters = () => {
    if (isCollectionsMode) setFilter(draft as KnowledgeBaseFilter);
    else setAllRecordsFilter(draft as AllRecordsFilter);
  };

  const resetFilter = () => {
    if (isCollectionsMode) clearFilter();
    else clearAllRecordsFilter();
    setDraft({});
  };

  const isDirty = managedFilterKey(draft) !== managedFilterKey(activeFilter);

  // Get available filters from store (returned by API)
  const availableFilters = isCollectionsMode
    ? tableData?.filters?.available
    : allRecordsTableData?.filters?.available;

  // Helper to get icon for record type
  const getIconForRecordType = (recordType: string): string => {
    const iconMap: Record<string, string> = {
      FILE: 'description',
      DRIVE: 'add_to_drive',
      WEBPAGE: 'language',
      DATABASE: 'storage',
      DATASOURCE: 'dataset',
      MESSAGE: 'chat',
      MAIL: 'mail',
      GROUP_MAIL: 'mark_email_unread',
      TICKET: 'confirmation_number',
      COMMENT: 'comment',
      INLINE_COMMENT: 'mode_comment',
      CONFLUENCE_PAGE: 'article',
      CONFLUENCE_BLOGPOST: 'rss_feed',
      SHAREPOINT_PAGE: 'web',
      SHAREPOINT_LIST: 'list',
      SHAREPOINT_LIST_ITEM: 'list_alt',
      SHAREPOINT_DOCUMENT_LIBRARY: 'library_books',
      LINK: 'link',
      PROJECT: 'work',
      OTHERS: 'more_horiz',
    };
    return iconMap[recordType] || 'description';
  };

  // Helper to get icon color for indexing status
  const getColorForStatus = (status: string): string => {
    const colorMap: Record<string, string> = {
      COMPLETED: 'var(--accent-11)',
      IN_PROGRESS: 'var(--slate-11)',
      FAILED: 'var(--red-11)',
      NOT_STARTED: 'var(--sky-11)',
      QUEUED: 'var(--blue-9)',
      PAUSED: 'var(--slate-9)',
      FILE_TYPE_NOT_SUPPORTED: 'var(--amber-11)',
      AUTO_INDEX_OFF: 'var(--slate-8)',
      EMPTY: 'var(--slate-9)',
      ENABLE_MULTIMODAL_MODELS: 'var(--slate-9)',
      PASSWORD_PROTECTED: 'var(--amber-11)',
      CONNECTOR_DISABLED: 'var(--slate-9)',
    };
    return colorMap[status] || 'var(--slate-9)';
  };

  // Dynamic type options from API
  const typeOptions = useMemo(() => {
    if (!availableFilters?.recordTypes) return [];
    return availableFilters.recordTypes.map((opt) => ({
      value: opt.id,
      label: opt.label,
      icon: getIconForRecordType(opt.id),
    }));
  // Dep: whole `availableFilters` — React Compiler (react-hooks/preserve-manual-memoization)
  // disagrees with optional-chained field deps like [availableFilters?.recordTypes].
  }, [availableFilters]);

  // Dynamic status options from API
  const statusOptions = useMemo(() => {
    if (!availableFilters?.indexingStatus) return [];
    return availableFilters.indexingStatus.map((opt) => ({
      value: opt.id,
      label: opt.label,
      icon: getIndexStatusIcon(opt.id),
      iconColor: getColorForStatus(opt.id),
      ...(opt.id === 'IN_PROGRESS' && {
        customIcon: <LapTimerIcon size={20} color={getColorForStatus(opt.id)}/>,
      }),
    }));
  }, [availableFilters]);

  // Dynamic source options from API (All Records only - origins only)
  const sourceOptions = useMemo(() => {
    if (!availableFilters?.origins) return [];
    return availableFilters.origins.map((opt) => ({
      value: opt.id,
      label: opt.id === 'COLLECTION' ? 'Collections' : opt.label,
      icon: opt.id === 'COLLECTION' ? 'folder' : 'cloud',
    }));
  }, [availableFilters]);

  const connectorOptions = useMemo(() => {
    if (!availableFilters?.connectors) return [];
    return availableFilters.connectors.map((opt) => ({
      value: opt.id,
      label: opt.label,
      icon: 'hub',
    }));
  }, [availableFilters]);

  // Common size options (hardcoded - not from API)
  const SIZE_OPTIONS = [
    { value: 'lt1mb', label: t('filter.lt1mb') },
    { value: '1to10mb', label: t('filter.1to10mb') },
    { value: '10to100mb', label: t('filter.10to100mb') },
  ];

  // Unified handlers — work for both modes via updateFilter
  const handleTypeChange = (values: string[]) => updateFilter({ recordTypes: values as RecordType[] });

  const handleStatusChange = (values: string[]) => updateFilter({ indexingStatus: values as IndexingStatus[] });

  const handleSourceChange = (values: string[]) => updateFilter({ origins: values as NodeOrigin[] });

  const handleConnectorChange = (values: string[]) =>
    updateFilter({ connectorIds: values });

  const handleSizeChange = (values: string[]) => updateFilter({ sizeRange: values[0] as SizeRange | undefined });

  const handleCreatedDateApply = (
    startDate: string,
    endDate: string | undefined,
    dateType: DateFilterType
  ) => {
    updateFilter({
      createdAfter: dateType === 'before' ? undefined : startDate,
      createdBefore: dateType === 'after' ? undefined
        : dateType === 'on' ? startDate
        : endDate || startDate,
      createdDateType: dateType,
    });
  };

  const handleCreatedDateClear = () => {
    updateFilter({ createdAfter: undefined, createdBefore: undefined, createdDateType: undefined });
  };

  const handleUpdatedDateApply = (
    startDate: string,
    endDate: string | undefined,
    dateType: DateFilterType
  ) => {
    updateFilter({
      updatedAfter: dateType === 'before' ? undefined : startDate,
      updatedBefore: dateType === 'after' ? undefined
        : dateType === 'on' ? startDate
        : endDate || startDate,
      updatedDateType: dateType,
    });
  };

  const handleUpdatedDateClear = () => {
    updateFilter({ updatedAfter: undefined, updatedBefore: undefined, updatedDateType: undefined });
  };

  const hasAnyFilter = filterHasValues(draft) || filterHasValues(activeFilter);

  const actions = (
    <>
      {isDirty && (
        <Button
          type="button"
          size="1"
          variant="solid"
          onClick={applyFilters}
          style={{
            height: '26px',
            minHeight: isMobile ? '44px' : undefined,
            cursor: 'pointer',
          }}
        >
          {t('filter.applyFilters')}
        </Button>
      )}

      {/* Clear Filter */}
      {hasAnyFilter && (
        <Flex
          align="center"
          gap="1"
          onClick={resetFilter}
          style={{
            height: 32,
            padding: '0 8px',
            cursor: 'pointer',
          }}
        >
          <MaterialIcon name="close" size={14} color="var(--neutral-11)" />
          <Text size="2" style={{ color: 'var(--neutral-11)', fontWeight: 400, lineHeight: '20px', whiteSpace: 'nowrap' }}>
            {t('filter.clearFilter')}
          </Text>
        </Flex>
      )}
    </>
  );

  const scroller = (
    <Flex
      align="center"
      gap="2"
      className={scrollRow ? 'kb-filter-bar-mobile no-scrollbar' : undefined}
      style={
        scrollRow
          ? { flex: 1, minWidth: 0, overflowX: 'auto', flexWrap: 'nowrap' }
          : {
              minHeight: 0,
              padding: '8px 16px',
              borderBottom: '1px solid var(--olive-3)',
              backgroundColor: 'var(--olive-2)',
              backdropFilter: 'var(--frost-filter-subtle)',
              flexWrap: 'wrap',
            }
      }
    >
      {!isMobile && <MaterialIcon name="filter_list" size={16} color="var(--neutral-11)" />}

      {/* Type Filter */}
      <FilterDropdown
        label={t('filter.type')}
        icon="source"
        options={typeOptions}
        selectedValues={draft.recordTypes || []}
        onSelectionChange={handleTypeChange}
        disabled={typeOptions.length === 0}
        searchable
        pluralLabel={t('filter.types')}
      />

      {/* Status Filter */}
      <FilterDropdown
        label={t('filter.status')}
        icon="contrast"
        options={statusOptions}
        selectedValues={draft.indexingStatus || []}
        onSelectionChange={handleStatusChange}
        disabled={statusOptions.length === 0}
        pluralLabel={t('filter.statuses')}
      />

      {/* Source Filter — All Records mode only */}
      {!isCollectionsMode && (
        <FilterDropdown
          label={t('filter.source')}
          icon="cloud_upload"
          options={sourceOptions}
          selectedValues={draft.origins || []}
          onSelectionChange={handleSourceChange}
          searchable
          disabled={sourceOptions.length === 0}
          pluralLabel={t('filter.sources')}
        />
      )}

      {/* Connector instances — All Records only */}
      {!isCollectionsMode && (
        <FilterDropdown
          label={t('filter.connector')}
          icon="hub"
          options={connectorOptions}
          selectedValues={draft.connectorIds || []}
          onSelectionChange={handleConnectorChange}
          searchable
          disabled={
            connectorOptions.length === 0 && (draft.connectorIds?.length ?? 0) === 0
          }
          pluralLabel={t('filter.connectors', { defaultValue: 'Connectors' })}
        />
      )}

      {/* Size Filter */}
      <FilterDropdown
        label={t('filter.size')}
        icon="layers"
        options={SIZE_OPTIONS}
        selectedValues={draft.sizeRange ? [draft.sizeRange] : []}
        onSelectionChange={handleSizeChange}
        pluralLabel={t('filter.sizes')}
        selectionMode="single"
      />

      {/* Date Created Filter */}
      <DateRangePicker
        label={t('filter.dateCreated')}
        icon="calendar_today"
        startDate={draft.createdAfter}
        endDate={draft.createdBefore}
        dateType={draft.createdDateType}
        onApply={handleCreatedDateApply}
        onClear={handleCreatedDateClear}
        defaultDateType="between"
      />

      {/* Last Updated Filter */}
      <DateRangePicker
        label={t('filter.lastUpdated')}
        icon="calendar_today"
        startDate={draft.updatedAfter}
        endDate={draft.updatedBefore}
        dateType={draft.updatedDateType}
        onApply={handleUpdatedDateApply}
        onClear={handleUpdatedDateClear}
        defaultDateType="between"
      />

      {/* Sort applies at once, like the column headers; it follows the last filter */}
      <KbSortMenu
        value={isCollectionsMode ? sort : allRecordsSort}
        onChange={(next) => (isCollectionsMode ? setSort(next) : setAllRecordsSort(next))}
      />

      {!scrollRow && actions}
    </Flex>
  );

  if (!scrollRow) return scroller;

  // Apply and Clear stay outside the scroller so they never slide off-screen.
  return (
    <Flex
      align="center"
      gap="2"
      style={{
        minHeight: 0,
        padding: isMobile ? 'var(--space-2)' : '8px 16px',
        borderBottom: '1px solid var(--olive-3)',
        backgroundColor: 'var(--olive-2)',
        backdropFilter: 'var(--frost-filter-subtle)',
      }}
    >
      {scroller}
      {(isDirty || hasAnyFilter) && (
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {actions}
        </Flex>
      )}
    </Flex>
  );
}
