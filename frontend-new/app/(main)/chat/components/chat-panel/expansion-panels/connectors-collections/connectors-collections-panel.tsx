'use client';

import React from 'react';
import { Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { CollectionsTab } from './collections-tab';
import { useTranslation } from 'react-i18next';

/** View mode controlling which container wraps this panel */
type ExpansionViewMode = 'inline' | 'overlay';

interface ConnectorsCollectionsPanelProps {
  selectedKbIds: string[];
  onSelectionChange: (kbIds: string[]) => void;
  /** Toggles between inline and overlay view */
  onToggleView?: () => void;
  /** Current view mode — controls which icon the toggle button shows */
  viewMode?: ExpansionViewMode;
}

/**
 * Panel for selecting Collections to scope a chat query.
 * Renders inside either ChatInputExpansionPanel (inline) or
 * ChatInputOverlayPanel (overlay) — the parent decides which container.
 *
 * Shows a single unified view: collections list with local search.
 */
export function ConnectorsCollectionsPanel({
  selectedKbIds,
  onSelectionChange,
  onToggleView,
  viewMode = 'inline',
}: ConnectorsCollectionsPanelProps) {
  const { t } = useTranslation();

  const handleToggleKb = (kbId: string) => {
    const next = selectedKbIds.includes(kbId)
      ? selectedKbIds.filter((id) => id !== kbId)
      : [...selectedKbIds, kbId];
    onSelectionChange(next);
  };

  return (
    <Flex direction="column" gap="4" style={{ flex: 1, overflow: 'hidden' }}>
      {/* Header: title + expand/collapse toggle */}
      <Flex align="center" justify="between" style={{ width: '100%' }}>
        <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('nav.collections')}
        </Text>
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onToggleView}
          style={{ cursor: onToggleView ? 'pointer' : 'default' }}
        >
          <MaterialIcon
            name={viewMode === 'overlay' ? 'close_fullscreen' : 'open_in_full'}
            size={18}
            color="var(--slate-9)"
          />
        </IconButton>
      </Flex>

      <CollectionsTab
        selectedKbIds={selectedKbIds}
        onToggleKb={handleToggleKb}
      />
    </Flex>
  );
}
