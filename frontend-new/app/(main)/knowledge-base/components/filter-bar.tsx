'use client';

import React, { useMemo } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon, FilterDropdown, DateRangePicker, type DateFilterType } from '@/app/components/ui';
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

interface KBFilterBarProps {
  pageViewMode: PageViewMode;
}

export function FilterBar({ pageViewMode }: KBFilterBarProps) {
  const { t } = useTranslation();
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
  } = useKnowledgeBaseStore();

  const isCollectionsMode = pageViewMode === 'collections';

  // Active filter and actions based on current mode
  const activeFilter = isCollectionsMode ? filter : allRecordsFilter;

  const updateFilter = (patch: Partial<KnowledgeBaseFilter> | Partial<AllRecordsFilter>) => {
    if (isCollectionsMode) setFilter(patch as Partial<KnowledgeBaseFilter>);
    else setAllRecordsFilter(patch as Partial<AllRecordsFilter>);
  };

  const resetFilter = isCollectionsMode ? clearFilter : clearAllRecordsFilter;

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

  const handleSizeChange = (values: string[]) => updateFilter({ sizeRanges: values as SizeRange[] });

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

  const hasAnyFilter = !!(
    (activeFilter.recordTypes?.length ?? 0) > 0 ||
    (activeFilter.indexingStatus?.length ?? 0) > 0 ||
    (activeFilter.origins?.length ?? 0) > 0 ||
    (activeFilter.sizeRanges?.length ?? 0) > 0 ||
    activeFilter.createdAfter || activeFilter.createdBefore ||
    activeFilter.updatedAfter || activeFilter.updatedBefore
  );

  return (
    <Flex
      align="center"
      gap="3"
      style={{
        minHeight: '40px',
        padding: 'var(--space-2) var(--space-4)',
        borderBottom: '1px solid var(--olive-3)',
        backgroundColor: 'var(--olive-2)',
        backdropFilter: 'blur(8px)',
        flexWrap: 'wrap',
      }}
    >
      <MaterialIcon name="filter_list" size={16} color="var(--slate-9)" />

      {/* Type Filter */}
      <FilterDropdown
        label={t('filter.type')}
        icon="source"
        options={typeOptions}
        selectedValues={activeFilter.recordTypes || []}
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
        selectedValues={activeFilter.indexingStatus || []}
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
          selectedValues={activeFilter.origins || []}
          onSelectionChange={handleSourceChange}
          searchable
          disabled={sourceOptions.length === 0}
          pluralLabel={t('filter.sources')}
        />
      )}

      {/* Size Filter */}
      <FilterDropdown
        label={t('filter.size')}
        icon="layers"
        options={SIZE_OPTIONS}
        selectedValues={activeFilter.sizeRanges || []}
        onSelectionChange={handleSizeChange}
        pluralLabel={t('filter.sizes')}
      />

      {/* Date Created Filter */}
      <DateRangePicker
        label={t('filter.dateCreated')}
        icon="calendar_today"
        startDate={activeFilter.createdAfter}
        endDate={activeFilter.createdBefore}
        dateType={activeFilter.createdDateType}
        onApply={handleCreatedDateApply}
        onClear={handleCreatedDateClear}
        defaultDateType="between"
      />

      {/* Last Updated Filter */}
      <DateRangePicker
        label={t('filter.lastUpdated')}
        icon="calendar_today"
        startDate={activeFilter.updatedAfter}
        endDate={activeFilter.updatedBefore}
        dateType={activeFilter.updatedDateType}
        onApply={handleUpdatedDateApply}
        onClear={handleUpdatedDateClear}
        defaultDateType="between"
      />

      {/* Clear Filter */}
      {hasAnyFilter && (
        <Flex
          align="center"
          onClick={resetFilter}
          style={{
            height: '26px',
            padding: '0 var(--space-2)',
            borderRadius: 'var(--radius-2)',
            border: '1px dashed var(--gray-a7)',
            cursor: 'pointer',
            gap: '4px',
          }}
        >
          <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
            {t('filter.clearFilter')}
          </Text>
          <MaterialIcon name="backspace" size={16} color="var(--gray-11)" />
        </Flex>
      )}
    </Flex>
  );
}
