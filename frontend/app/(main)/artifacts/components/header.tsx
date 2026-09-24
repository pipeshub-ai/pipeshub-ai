'use client';

import { Flex, IconButton, Button, SegmentedControl } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar';
import { useTranslation } from 'react-i18next';
import type { ArtifactsViewMode } from '../types';

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
        padding: isMobile
          ? `var(--space-3) var(--space-4) var(--space-3) ${MOBILE_HAMBURGER_GUTTER_PX}px`
          : 'var(--space-3) var(--space-4)',
        borderBottom: '1px solid var(--olive-3)',
        flexShrink: 0,
      }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <h1
          style={{
            margin: 0,
            fontSize: 'var(--font-size-3)',
            fontWeight: 'var(--font-weight-medium)',
            lineHeight: 'var(--line-height-3)',
            letterSpacing: 'var(--letter-spacing-3)',
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
            aria-label={t('action.find')}
          >
            <MaterialIcon name="close" size={18} />
          </IconButton>
        ) : (
          <Button variant="ghost" size="1" color="gray" onClick={onFind}>
            <MaterialIcon name="search" size={16} />
            {t('action.find')}
          </Button>
        )}
        <IconButton
          variant="ghost"
          size="2"
          color="gray"
          onClick={onRefresh}
          aria-label={t('action.refresh')}
        >
          <MaterialIcon name="refresh" size={18} />
        </IconButton>
        <SegmentedControl.Root
          value={viewMode}
          onValueChange={(value) => onViewModeChange(value as ArtifactsViewMode)}
        >
          <SegmentedControl.Item value="grid">
            <MaterialIcon name="grid_view" size={16} />
          </SegmentedControl.Item>
          <SegmentedControl.Item value="list">
            <MaterialIcon name="view_list" size={16} />
          </SegmentedControl.Item>
        </SegmentedControl.Root>
      </Flex>
    </Flex>
  );
}
