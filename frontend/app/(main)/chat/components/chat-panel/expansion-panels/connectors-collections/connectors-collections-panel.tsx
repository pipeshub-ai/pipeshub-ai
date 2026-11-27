'use client';

import React, { useMemo, useState } from 'react';
import { Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SegmentedTabs } from '@/app/components/ui/segmented-tabs';
import { FilterPanelFooterExtras, useFilterPanelExtraTab } from '@/config';
import { CollectionsTab, type CollectionScopeSelection } from './collections-tab';
import { useTranslation } from 'react-i18next';

/** View mode controlling which container wraps this panel */
type ExpansionViewMode = 'inline' | 'overlay';

type PanelView = 'sources' | 'extra';

interface ConnectorsCollectionsPanelProps {
  selection: CollectionScopeSelection;
  onSelectionChange: (next: CollectionScopeSelection) => void;
  /** Toggles between inline and overlay view */
  onToggleView?: () => void;
  /** Current view mode — controls which icon the toggle button shows */
  viewMode?: ExpansionViewMode;
  /** Closes the panel (passed to the extra view). */
  onClose?: () => void;
}

/**
 * Panel for selecting connectors and collections to scope a chat query.
 * Renders inside either ChatInputExpansionPanel (inline) or
 * ChatInputOverlayPanel (overlay) — the parent decides which container.
 *
 * Shows a single unified view: collections list with local search, plus the
 * tab an edition may add (useFilterPanelExtraTab), offered as a second view.
 */
export function ConnectorsCollectionsPanel({
  selection,
  onSelectionChange,
  onToggleView,
  viewMode = 'inline',
  onClose,
}: ConnectorsCollectionsPanelProps) {
  const { t } = useTranslation();
  const extraTab = useFilterPanelExtraTab();
  const [view, setView] = useState<PanelView>(extraTab?.openFirst ? 'extra' : 'sources');
  const title = t('chat.connectorsCollectionsTitle', { defaultValue: 'Connectors and Collections' });
  const views = useMemo(
    () => [
      { value: 'extra' as PanelView, label: extraTab?.label ?? '' },
      { value: 'sources' as PanelView, label: title },
    ],
    [title, extraTab?.label]
  );
  const showExtra = extraTab !== null && view === 'extra';

  return (
    <Flex direction="column" gap="4" style={{ flex: 1, overflow: 'hidden' }}>
      {/* Header: title (or the views, when an edition adds one) + expand/collapse toggle */}
      <Flex align="center" justify="between" style={{ width: '100%' }}>
        {extraTab ? (
          <SegmentedTabs<PanelView> kind="tabs" size="1" aria-label={title} value={view} onValueChange={setView} items={views} />
        ) : (
          <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Text>
        )}
        <IconButton
          aria-label={viewMode === 'overlay' ? t('recordView.exitFullscreen') : t('recordView.openFullscreen')}
          variant="ghost"
          color="gray"
          size="1"
          onClick={onToggleView}
          style={{
            cursor: onToggleView ? 'pointer' : 'default',
            flexShrink: 0,
            // Radix pulls ghost buttons out by their padding; the header has its own.
            margin: 0,
            padding: 0,
            width: 32,
            height: 32,
          }}
        >
          <MaterialIcon
            name={viewMode === 'overlay' ? 'close_fullscreen' : 'open_in_full'}
            size={16}
            color="var(--slate-11)"
          />
        </IconButton>
      </Flex>

      {showExtra && extraTab ? (
        <extraTab.Panel selection={selection} onSelectionChange={onSelectionChange} onClose={onClose} />
      ) : (
        <CollectionsTab selection={selection} onSelectionChange={onSelectionChange} />
      )}

      <FilterPanelFooterExtras selection={selection} onSelectionChange={onSelectionChange} />
    </Flex>
  );
}
