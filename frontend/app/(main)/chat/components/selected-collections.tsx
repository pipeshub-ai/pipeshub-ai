'use client';

import React, { useRef, useState, useCallback, useEffect, useSyncExternalStore } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
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

/** Every attachment chip is the same 28px tall, with a 260px cap. */
export const ATTACHMENT_CHIP_HEIGHT = 'calc(var(--space-5) + var(--space-1))';
export const ATTACHMENT_CHIP_MAX_WIDTH = 'calc(var(--space-9) * 4 + var(--space-1))';

/** Room above and below the rail for the corner remove buttons, which sit outside their chips. */
const RAIL_OVERHANG = 'var(--space-2)';
const RAIL_SCROLL_STEP_PX = 220;

const COARSE_POINTER_QUERY = '(pointer: coarse)';
const subscribeCoarse = (onChange: () => void) => {
  const mq = typeof window !== 'undefined' ? window.matchMedia?.(COARSE_POINTER_QUERY) : undefined;
  if (!mq) return () => {};
  mq.addEventListener('change', onChange);
  return () => mq.removeEventListener('change', onChange);
};
const coarseSnapshot = () => typeof window !== 'undefined' && !!window.matchMedia?.(COARSE_POINTER_QUERY)?.matches;

/** True on a touch screen, where there is no hover to reveal a control. */
export function useCoarsePointer(): boolean {
  return useSyncExternalStore(subscribeCoarse, coarseSnapshot, () => false);
}

/**
 * Hover and keyboard-focus state for a chip whose remove button is revealed rather than always drawn.
 * Spread `handlers` on the chip's outer box.
 */
export function useRevealOnHoverOrFocus() {
  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const handlers = {
    onMouseEnter: () => setHovered(true),
    onMouseLeave: () => setHovered(false),
    onFocus: () => setFocusWithin(true),
    onBlur: (event: React.FocusEvent<HTMLElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusWithin(false);
    },
  };
  return { hovered, revealed: hovered || focusWithin, handlers };
}

/**
 * The corner remove button every attachment chip shares: an 18px high-contrast circle over the chip's top-right
 * corner. Always in the DOM so Tab reaches it; drawn on hover, on keyboard focus, and always on a touch screen.
 * A transparent border grows its hit area to 24px around the drawn circle (44px on touch screens).
 */
export function AttachmentRemoveButton({
  label,
  onRemove,
  visible,
}: {
  label: string;
  onRemove: () => void;
  visible: boolean;
}) {
  const coarse = useCoarsePointer();
  const shown = visible || coarse;
  return (
    <IconButton
      size="1"
      variant="solid"
      color="gray"
      highContrast
      className="touch-hit"
      data-chip-remove=""
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation();
        onRemove();
      }}
      style={{
        position: 'absolute',
        // The 24px box sits 9px over the corner, so the 18px circle inside it sits 6px over.
        top: 'calc(var(--space-1) / -4 - var(--space-2))',
        right: 'calc(var(--space-1) / -4 - var(--space-2))',
        width: 'var(--space-5)',
        height: 'var(--space-5)',
        minWidth: 'var(--space-5)',
        boxSizing: 'border-box',
        border: 'calc(var(--space-1) * 0.75) solid transparent',
        backgroundClip: 'padding-box',
        padding: 0,
        margin: 0,
        borderRadius: '50%',
        boxShadow: 'var(--shadow-2)',
        zIndex: 1,
        opacity: shown ? 1 : 0,
        transition: 'opacity var(--motion-micro) var(--motion-ease)',
      }}
    >
      <MaterialIcon name="close" size={12} />
    </IconButton>
  );
}

interface CollectionPillProps {
  item: SelectedItem;
  /** Show the remove button — in the composer; a sent message's chips have none */
  removable?: boolean;
  onRemove?: (id: string) => void;
}

/** One selected source: its mark and its name on a 28px chip. */
function CollectionPill({ item, removable = false, onRemove }: CollectionPillProps) {
  const { t } = useTranslation();
  const { revealed, handlers } = useRevealOnHoverOrFocus();
  return (
    <Flex
      align="center"
      gap="2"
      {...(removable && onRemove ? handlers : null)}
      style={{
        position: 'relative',
        flexShrink: 0,
        boxSizing: 'border-box',
        height: ATTACHMENT_CHIP_HEIGHT,
        maxWidth: ATTACHMENT_CHIP_MAX_WIDTH,
        paddingLeft: 'var(--space-2)',
        paddingRight: 'calc(var(--space-2) + var(--space-1) / 2)',
        backgroundColor: 'var(--surface-raised)',
        border: '1px solid var(--border-strong)',
        borderRadius: 'var(--radius-4)',
      }}
    >
      <Flex align="center" justify="center" aria-hidden style={{ flexShrink: 0 }}>
        <KbNodeNameIcon
          isKnowledgeHub
          nodeType={(item.nodeType as NodeType) || (item.kind === 'connector' ? 'app' : item.kind === 'file' ? 'record' : 'folder')}
          connector={item.connector || item.connectorType}
          extension={item.extension}
          mimeType={item.mimeType}
          name={item.name}
          size={16}
        />
      </Flex>
      <Text
        size="1"
        weight="medium"
        style={{
          color: 'var(--text-primary)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          minWidth: 0,
        }}
      >
        {item.name}
      </Text>
      {removable && onRemove && (
        <AttachmentRemoveButton
          label={t('chat.removeSelectedSource', { name: item.name })}
          onRemove={() => onRemove(item.id)}
          visible={revealed}
        />
      )}
    </Flex>
  );
}

function RailArrow({ dir, onClick }: { dir: 'left' | 'right'; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <IconButton
      size="1"
      variant="ghost"
      color="gray"
      aria-label={dir === 'left' ? t('chat.scrollAttachmentsLeft') : t('chat.scrollAttachmentsRight')}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      style={{
        position: 'absolute',
        [dir]: 0,
        top: '50%',
        transform: 'translateY(-50%)',
        width: 'var(--space-5)',
        height: 'var(--space-5)',
        minWidth: 'var(--space-5)',
        margin: 0,
        padding: 0,
        borderRadius: '50%',
        background: 'var(--color-background)',
        boxShadow: 'var(--shadow-2)',
        zIndex: 2,
      }}
    >
      <MaterialIcon name={dir === 'left' ? 'chevron_left' : 'chevron_right'} size={16} color="var(--text-secondary)" />
    </IconButton>
  );
}

// ── Exported list components ──

interface SelectedCollectionsProps {
  /** Collections to render as chips */
  collections: SelectedItem[];
  /** Whether each chip has a remove button */
  removable?: boolean;
  /** Called when a chip's remove button is pressed */
  onRemove?: (id: string) => void;
  /** More chips on the same rail after the sources (pasted text in the composer). */
  children?: React.ReactNode;
  /** No chip is left to take focus after a remove: where focus goes instead (the composer's editor). */
  onEmptied?: () => void;
}

/**
 * One line of attachment chips that scrolls sideways, with an arrow on whichever side has more. It never wraps:
 * the clipped last chip says there is more.
 *
 * Used in two places:
 * 1. **Chat input area** — the selected sources (and pastes) above the text, each with its remove button.
 * 2. **Chat response** — which sources a message was scoped to (read-only).
 */
export function SelectedCollections({
  collections,
  removable = false,
  onRemove,
  children,
  onEmptied,
}: SelectedCollectionsProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const childCount = React.Children.toArray(children).length;
  const hasChildren = childCount > 0;

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 1);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateScrollState();
    el.addEventListener('scroll', updateScrollState);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(updateScrollState) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener('scroll', updateScrollState);
      ro?.disconnect();
    };
    // Keyed on the chip count, not the arrays: the composer re-renders the rail on every keystroke with new arrays,
    // and re-subscribing then would re-measure and set state after every commit while someone types.
  }, [collections.length, childCount, updateScrollState]);

  const scrollBy = (direction: 'left' | 'right') => {
    const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    scrollRef.current?.scrollBy({
      left: direction === 'left' ? -RAIL_SCROLL_STEP_PX : RAIL_SCROLL_STEP_PX,
      behavior: reduce ? 'auto' : 'smooth',
    });
  };

  /**
   * A chip removed from the keyboard hands focus to the remove button now at its place (or the last one), and to
   * the editor once no chip is left. A mouse remove goes back to the editor. Never to the page.
   */
  const handleRemoveClickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
    const button = (event.target as Element).closest('[data-chip-remove]');
    const rail = scrollRef.current;
    if (!button || !rail) return;
    const fromKeyboard = event.detail === 0;
    const index = Array.from(rail.querySelectorAll('[data-chip-remove]')).indexOf(button);
    setTimeout(() => {
      const left = rail.isConnected ? Array.from(rail.querySelectorAll<HTMLElement>('[data-chip-remove]')) : [];
      if (fromKeyboard && left.length > 0) left[Math.min(index, left.length - 1)]?.focus();
      else onEmptied?.();
    }, 0);
  };

  if ((!collections || collections.length === 0) && !hasChildren) return null;

  return (
    <Box style={{ position: 'relative', width: '100%', maxWidth: '100%', minWidth: 0 }}>
      <Flex
        ref={scrollRef}
        role="group"
        aria-label={t('chat.attachmentsGroup')}
        align="center"
        gap="2"
        wrap="nowrap"
        onClickCapture={removable ? handleRemoveClickCapture : undefined}
        style={{
          overflowX: 'auto',
          minWidth: 0,
          width: '100%',
          boxSizing: 'border-box',
          paddingTop: RAIL_OVERHANG,
          paddingBottom: RAIL_OVERHANG,
          marginTop: `calc(${RAIL_OVERHANG} * -1)`,
          marginBottom: `calc(${RAIL_OVERHANG} * -1)`,
          // Room on the right so the last chip's remove button is not cut off.
          paddingRight: RAIL_OVERHANG,
        }}
      >
        {collections.map((col) => (
          <CollectionPill key={col.id} item={col} removable={removable} onRemove={onRemove} />
        ))}
        {children}
      </Flex>
      {canScrollLeft && <RailArrow dir="left" onClick={() => scrollBy('left')} />}
      {canScrollRight && <RailArrow dir="right" onClick={() => scrollBy('right')} />}
    </Box>
  );
}
