'use client';

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KnowledgeItemIcon } from '@/app/components/ui/knowledge-item-icon';
import type { ConnectorType } from '@/app/components/ui/ConnectorIcon';

export interface SelectedItem {
  id: string;
  name: string;
  /** 'connector' for hub apps/connectors, 'collection' for KB record groups */
  kind?: 'collection' | 'connector';
  /** Resolved connector type key — used to render the right connector icon */
  connectorType?: string;
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
  return (
    <Flex
      align="center"
      gap="1"
      style={{
        backgroundColor: 'var(--olive-a2)',
        border: '1px solid var(--slate-4)',
        borderRadius: 'var(--radius-full)',
        padding: removable ? 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)' : 'var(--space-2) var(--space-3)',
        maxWidth: '200px',
        flexShrink: 0,
      }}
    >
      <KnowledgeItemIcon
        kind={item.kind ?? 'collection'}
        connectorType={item.connectorType as ConnectorType | undefined}
        size={16}
      />
      <Text
        size="2"
        style={{
          color: 'var(--slate-11)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {item.name}
      </Text>
      {removable && onRemove && (
        <IconButton
          variant="ghost"
          size="1"
          onClick={() => onRemove(item.id)}
          style={{
            width: '16px',
            height: '16px',
            minWidth: '16px',
            padding: 0,
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="close" size={14} color="var(--slate-9)" />
        </IconButton>
      )}
    </Flex>
  );
}

// ── Exported list components ──

interface SelectedCollectionsProps {
  /** Collections to render as pills */
  collections: SelectedItem[];
  /** Whether each pill shows a × remove button */
  removable?: boolean;
  /** Called when a pill's × is clicked */
  onRemove?: (id: string) => void;
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
}: SelectedCollectionsProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateScrollState();
    el.addEventListener('scroll', updateScrollState);
    const ro = new ResizeObserver(updateScrollState);
    ro.observe(el);
    return () => {
      el.removeEventListener('scroll', updateScrollState);
      ro.disconnect();
    };
  }, [collections, updateScrollState]);

  const scrollBy = (direction: 'left' | 'right') => {
    scrollRef.current?.scrollBy({
      left: direction === 'left' ? -200 : 200,
      behavior: 'smooth',
    });
  };

  if (!collections || collections.length === 0) return null;

  return (
    <Flex align="center" style={{ minWidth: 0 }}>
      {canScrollLeft && (
        <IconButton
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
        style={{ overflowX: 'auto', minWidth: 0, flex: 1 }}
      >
        {collections.map((col) => (
          <CollectionPill
            key={col.id}
            item={col}
            removable={removable}
            onRemove={onRemove}
          />
        ))}
      </Flex>

      {canScrollRight && (
        <IconButton
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
