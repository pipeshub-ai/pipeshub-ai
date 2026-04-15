'use client';

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { KnowledgeItemIcon } from '@/app/components/ui/knowledge-item-icon';

export interface SelectedItem {
  id: string;
  name: string;
}

interface CollectionCardProps {
  item: SelectedItem;
  /** Show remove (×) button — used in the input area, hidden in response area */
  removable?: boolean;
  onRemove?: (id: string) => void;
}

/**
 * A single collection card (150×84 px) with an icon and truncated name.
 * Used in both the chat input area and the chat response area.
 */
function CollectionCard({ item, removable = false, onRemove }: CollectionCardProps) {
  return (
    <Flex
      direction="column"
      justify="between"
      style={{
        width: '150px',
        minWidth: '150px',
        height: '84px',
        backgroundColor: 'var(--olive-a2)',
        border: '1px solid var(--slate-3)',
        borderRadius: '3px',
        padding: 'var(--space-2)',
        overflow: 'hidden',
      }}
    >
      {/* Top: icon + optional close */}
      <Flex align="center" justify="between">
        <KnowledgeItemIcon kind="collection" size={20} />
        {removable && onRemove && (
          <IconButton
            variant="ghost"
            size="1"
            onClick={() => onRemove(item.id)}
            style={{
              width: '16px',
              height: '16px',
              padding: 0,
              cursor: 'pointer',
            }}
          >
            <MaterialIcon name="close" size={14} color="var(--slate-9)" />
          </IconButton>
        )}
      </Flex>

      {/* Bottom: name */}
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--slate-11)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          lineHeight: '16px',
        }}
      >
        {item.name}
      </Text>
    </Flex>
  );
}

// ── Exported list components ──

interface SelectedCollectionsProps {
  /** Collections to render as cards */
  collections: SelectedItem[];
  /** Whether each card shows a × remove button */
  removable?: boolean;
  /** Called when a card's × is clicked */
  onRemove?: (id: string) => void;
}

/**
 * Horizontally scrollable row of collection cards.
 *
 * Used in two places:
 * 1. **Chat input area** — shows selected KB filters with × to remove.
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
          <CollectionCard
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
