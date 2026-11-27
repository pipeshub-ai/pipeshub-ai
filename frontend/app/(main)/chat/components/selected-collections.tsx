'use client';

import React, { useRef } from 'react';
import { useScrollEdges } from '@/lib/hooks/use-scroll-edges';
import { Flex, Text, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KbNodeNameIcon } from '@/app/(main)/knowledge-base/utils/kb-node-name-icon';
import type { NodeType } from '@/app/(main)/knowledge-base/types';
import type { SelectedNodeKind } from '@/chat/utils/tree-selection';

export interface SelectedItem {
  id: string;
  name: string;
  /** 'connector' for hub apps, 'collection' for collections, 'folder' / 'file' for what is selected inside one */
  kind?: SelectedNodeKind;
  /** Resolved connector type key — used to render the right connector icon */
  connectorType?: string;
  nodeType?: string;
  /** Raw connector name, for the corner mark on a folder or file. */
  connector?: string;
  extension?: string;
  mimeType?: string;
}

interface CollectionPillProps {
  item: SelectedItem;
  /** Show remove (×) button — used in the input area, hidden in response area */
  removable?: boolean;
  onRemove?: (id: string) => void;
}

/**
 * A single filter pill with a connector-aware icon and truncated name.
 * Replaces the old 150×84 card style.
 */
function CollectionPill({ item, removable = false, onRemove }: CollectionPillProps) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      style={{
        backgroundColor: 'var(--olive-a2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        height: 28,
        padding: '0 6px',
        gap: 6,
        boxSizing: 'border-box',
        minWidth: '80px',
        maxWidth: '200px',
        flexShrink: 0,
      }}
    >
      <KbNodeNameIcon
        isKnowledgeHub
        nodeType={(item.nodeType as NodeType) || (item.kind === 'connector' ? 'app' : item.kind === 'file' ? 'record' : 'folder')}
        connector={item.connector || item.connectorType}
        extension={item.extension}
        mimeType={item.mimeType}
        name={item.name}
        size={16}
      />
      <Text
        size="2"
        style={{
          color: 'var(--slate-11)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          lineHeight: '20px',
          minWidth: 0,
        }}
      >
        {item.name}
      </Text>
      {removable && onRemove && (
        <IconButton
          variant="ghost"
          size="1"
          className="touch-hit"
          aria-label={t('chat.removeSelectedSource', { name: item.name })}
          onClick={() => onRemove(item.id)}
          style={{
            width: 16,
            height: 16,
            minWidth: 16,
            padding: 0,
            cursor: 'pointer',
            flexShrink: 0,
            // A short name leaves the pill wider than its content: keep the x at the right end.
            marginLeft: 'auto',
          }}
        >
          <MaterialIcon name="close" size={14} color="var(--slate-11)" />
        </IconButton>
      )}
    </Flex>
  );
}

// ── Exported list components ──

export interface SelectedCollectionsProps {
  /** Collections to render as pills */
  collections: SelectedItem[];
  /** Whether each pill shows a × remove button */
  removable?: boolean;
  /** Called when a pill's × is clicked */
  onRemove?: (id: string) => void;
  /** Chips placed before / after the pills, in the same scrolling strip. */
  leading?: React.ReactNode;
  trailing?: React.ReactNode;
  /** Wrap onto more lines instead of scrolling sideways (for a panel rather than the composer). */
  wrap?: boolean;
}

/**
 * Horizontally scrollable row of filter pills.
 *
 * Used in two places:
 * 1. **Chat input area** — shows selected KB / connector filters with × to remove.
 * 2. **Chat response** — shows which collections were scoped for a message (read-only).
 */
export function SelectedCollections({
  collections,
  removable = false,
  onRemove,
  leading,
  trailing,
  wrap = false,
}: SelectedCollectionsProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const { start: canScrollLeft, end: canScrollRight } = useScrollEdges(scrollRef, collections);

  const scrollBy = (direction: 'left' | 'right') => {
    scrollRef.current?.scrollBy({
      left: direction === 'left' ? -200 : 200,
      behavior: 'smooth',
    });
  };

  if ((!collections || collections.length === 0) && !leading && !trailing) return null;

  if (wrap) {
    return (
      <Flex align="center" gap="2" wrap="wrap" style={{ width: '100%', minWidth: 0 }}>
        {leading}
        {collections.map((col) => (
          <CollectionPill key={col.id} item={col} removable={removable} onRemove={onRemove} />
        ))}
        {trailing}
      </Flex>
    );
  }

  return (
    <Flex align="center" style={{ width: '100%', minWidth: 0, flex: 1 }}>
      {canScrollLeft && (
        <IconButton
          aria-label={t('common.scrollLeft')}
          variant="ghost"
          size="1"
          onClick={() => scrollBy('left')}
          style={{
            flexShrink: 0,
            cursor: 'pointer',
            width: '28px',
            height: '28px',
          }}
        >
          <MaterialIcon name="chevron_left" size={20} color="var(--slate-11)" />
        </IconButton>
      )}

      <Flex
        ref={scrollRef}
        align="center"
        gap="2"
        className="no-scrollbar"
        style={{ overflowX: 'auto', minWidth: 0, flex: 1, width: '100%', padding: 2 }}
      >
        {leading}
        {collections.map((col) => (
          <CollectionPill
            key={col.id}
            item={col}
            removable={removable}
            onRemove={onRemove}
          />
        ))}
        {trailing}
      </Flex>

      {canScrollRight && (
        <IconButton
          aria-label={t('common.scrollRight')}
          variant="ghost"
          size="1"
          onClick={() => scrollBy('right')}
          style={{
            flexShrink: 0,
            cursor: 'pointer',
            width: '28px',
            height: '28px',
          }}
        >
          <MaterialIcon name="chevron_right" size={20} color="var(--slate-11)" />
        </IconButton>
      )}
    </Flex>
  );
}
