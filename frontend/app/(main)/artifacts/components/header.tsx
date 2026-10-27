'use client';

import type React from 'react';
import { Flex, IconButton, Button, SegmentedControl } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar';
import { useTranslation } from 'react-i18next';
import type { ArtifactsViewMode } from '../types';

// Same toolbar metrics as the Collections header, so the two pages match.
const TEXT_BUTTON: React.CSSProperties = {
  cursor: 'pointer',
  height: 28,
  boxSizing: 'border-box',
  padding: '0 8px',
  gap: 6,
  margin: 0,
  fontSize: '13px',
  fontWeight: 400,
  color: 'var(--neutral-11)',
  borderRadius: 'var(--radius-2)',
};

interface ArtifactsHeaderProps {
  onFind: () => void;
  onRefresh: () => void;
  isSearchActive?: boolean;
  viewMode: ArtifactsViewMode;
  onViewModeChange: (mode: ArtifactsViewMode) => void;
}

export function ArtifactsHeader({
  onFind,
  onRefresh,
  isSearchActive,
  viewMode,
  onViewModeChange,
}: ArtifactsHeaderProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      style={{
        height: 40,
        padding: isMobile ? `0 var(--space-2) 0 ${MOBILE_HAMBURGER_GUTTER_PX}px` : '0 var(--space-3)',
        borderBottom: '1px solid var(--olive-3)',
        flexShrink: 0,
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <h1
          style={{
            margin: 0,
            fontSize: 'var(--font-size-2)',
            fontWeight: 'var(--font-weight-medium)',
            lineHeight: 'var(--line-height-2)',
            whiteSpace: 'nowrap',
            color: 'var(--slate-12)',
          }}
        >
          {t('nav.allArtifacts')}
        </h1>
        <BetaBadge />
      </Flex>
      <Flex align="center" gap="2">
        {isSearchActive ? (
          <IconButton
            variant="ghost"
            size="2"
            color="gray"
            onClick={onFind}
            aria-label={t('artifacts.closeSearch')}
          >
            <MaterialIcon name="close" size={18} />
          </IconButton>
        ) : isMobile ? (
          <IconButton className="touch-hit" variant="ghost" size="2" color="gray" onClick={onFind} aria-label={t('action.find')}>
            <MaterialIcon name="search" size={18} />
          </IconButton>
        ) : (
          <Button variant="ghost" size="1" color="gray" onClick={onFind} style={TEXT_BUTTON}>
            <MaterialIcon name="search" size={14} color="var(--neutral-11)" />
            {t('action.find')}
          </Button>
        )}
        {isMobile ? (
          <IconButton
            className="touch-hit"
            variant="ghost"
            size="2"
            color="gray"
            onClick={onRefresh}
            aria-label={t('action.refresh')}
          >
            <MaterialIcon name="refresh" size={18} />
          </IconButton>
        ) : (
          <Button variant="ghost" size="1" color="gray" onClick={onRefresh} style={TEXT_BUTTON}>
            <MaterialIcon name="refresh" size={14} color="var(--neutral-11)" />
            {t('action.refresh')}
          </Button>
        )}
        <SegmentedControl.Root
          size="1"
          value={viewMode}
          onValueChange={(value) => onViewModeChange(value as ArtifactsViewMode)}
        >
          <SegmentedControl.Item value="grid" aria-label={t('artifacts.gridView')} title={t('artifacts.gridView')}>
            <MaterialIcon name="grid_on" size={16} />
          </SegmentedControl.Item>
          <SegmentedControl.Item value="list" aria-label={t('artifacts.listView')} title={t('artifacts.listView')}>
            <MaterialIcon name="view_list" size={16} />
          </SegmentedControl.Item>
        </SegmentedControl.Root>
      </Flex>
    </Flex>
  );
}
