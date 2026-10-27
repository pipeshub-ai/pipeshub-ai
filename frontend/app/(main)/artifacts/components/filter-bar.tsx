'use client';

import { Box, Flex, SegmentedControl } from '@radix-ui/themes';
import { FilterDropdown, DateRangePicker, type DateFilterType } from '@/app/components/ui';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ARTIFACTS_OWNERSHIP_TABS, GALLERY_ARTIFACT_TYPES, type ArtifactsOwnership } from '../types';
import { useArtifactsStore } from '../store';

const TYPE_ICONS: Record<string, string> = {
  IMAGE: 'image',
  CHART: 'bar_chart',
  DOCUMENT: 'description',
  SPREADSHEET: 'table_chart',
  PRESENTATION: 'slideshow',
  DATA_FILE: 'dataset',
  CODE: 'code',
  CODE_OUTPUT: 'terminal',
  OTHER: 'draft',
};

function epochToIso(epoch?: number): string | undefined {
  if (epoch == null) return undefined;
  const date = new Date(epoch);
  if (Number.isNaN(date.getTime())) return undefined;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function isoToEpochStart(iso: string): number {
  return new Date(`${iso}T00:00:00`).getTime();
}

function isoToEpochEnd(iso: string): number {
  return new Date(`${iso}T23:59:59.999`).getTime();
}

export function ArtifactsFilterBar() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const artifactTypes = useArtifactsStore((s) => s.artifactTypes);
  const dateFrom = useArtifactsStore((s) => s.dateFrom);
  const dateTo = useArtifactsStore((s) => s.dateTo);
  const ownership = useArtifactsStore((s) => s.ownership);
  const counts = useArtifactsStore((s) => s.counts);
  const setArtifactTypes = useArtifactsStore((s) => s.setArtifactTypes);
  const setDateRange = useArtifactsStore((s) => s.setDateRange);
  const setOwnership = useArtifactsStore((s) => s.setOwnership);

  const typeOptions = GALLERY_ARTIFACT_TYPES.map((value) => ({
    value,
    label: t(`artifacts.types.${value}`, { defaultValue: value.replaceAll('_', ' ') }),
    icon: TYPE_ICONS[value] || 'description',
  }));

  const handleDateApply = (startDate: string, endDate: string | undefined, dateType: DateFilterType) => {
    if (dateType === 'on') {
      setDateRange(isoToEpochStart(startDate), isoToEpochEnd(startDate));
      return;
    }
    if (dateType === 'before') {
      setDateRange(undefined, isoToEpochEnd(startDate));
      return;
    }
    if (dateType === 'after') {
      setDateRange(isoToEpochStart(startDate), undefined);
      return;
    }
    setDateRange(
      isoToEpochStart(startDate),
      endDate ? isoToEpochEnd(endDate) : isoToEpochEnd(startDate),
    );
  };

  return (
    <Flex
      align="center"
      gap="2"
      wrap="wrap"
      style={{ padding: 'var(--space-2) var(--space-4)', flexShrink: 0 }}
    >
      {/* Radix gives every segment the widest label's width, so phones get short labels. */}
      <Box className="no-scrollbar" style={{ maxWidth: '100%', overflowX: 'auto' }}>
        <SegmentedControl.Root
          value={ownership}
          onValueChange={(value) => setOwnership(value as ArtifactsOwnership)}
          size={{ initial: '1', sm: '2' }}
        >
          {ARTIFACTS_OWNERSHIP_TABS.map((tab) => (
            <SegmentedControl.Item key={tab} value={tab}>
              {t(isMobile ? `artifacts.tabsShort.${tab}` : `artifacts.tabs.${tab}`)} ({counts[tab]})
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
      </Box>
      <FilterDropdown
        label={t('filter.type')}
        icon="category"
        options={typeOptions}
        selectedValues={artifactTypes}
        onSelectionChange={setArtifactTypes}
        pluralLabel={t('filter.type')}
      />
      <DateRangePicker
        label={t('filter.dateCreated')}
        icon="calendar_today"
        startDate={epochToIso(dateFrom)}
        endDate={epochToIso(dateTo)}
        dateType={dateFrom && dateTo && epochToIso(dateFrom) !== epochToIso(dateTo) ? 'between' : dateFrom && dateTo ? 'on' : dateFrom ? 'after' : dateTo ? 'before' : undefined}
        onApply={handleDateApply}
        onClear={() => setDateRange(undefined, undefined)}
      />
    </Flex>
  );
}
