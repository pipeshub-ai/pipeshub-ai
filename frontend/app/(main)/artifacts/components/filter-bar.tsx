'use client';

import { Flex } from '@radix-ui/themes';
import { FilterDropdown, DateRangePicker, type DateFilterType } from '@/app/components/ui';
import { useTranslation } from 'react-i18next';
import { GALLERY_ARTIFACT_TYPES } from '../types';
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
  const artifactTypes = useArtifactsStore((s) => s.artifactTypes);
  const dateFrom = useArtifactsStore((s) => s.dateFrom);
  const dateTo = useArtifactsStore((s) => s.dateTo);
  const setArtifactTypes = useArtifactsStore((s) => s.setArtifactTypes);
  const setDateRange = useArtifactsStore((s) => s.setDateRange);

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
