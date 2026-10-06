'use client';

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Popover, Text } from '@radix-ui/themes';
import { Popover as PopoverPrimitive } from 'radix-ui';
import { ComposerTips } from './composer-tips';
import type { Mentionable, MentionGroup } from './use-mentionables';

export interface MentionAnchorRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface MentionPopoverProps {
  open: boolean;
  anchorRect: MentionAnchorRect | null;
  items: Mentionable[];
  activeIndex: number;
  listboxId: string;
  loading?: boolean;
  onSelect(item: Mentionable): void;
  onActiveChange(index: number): void;
  onDismiss(): void;
  /** Escape pressed while open. Radix sees it first (document capture) and would swallow it, so the editor never would. */
  onEscape(event: KeyboardEvent): void;
}

/** The keyboard-active option follows the arrows when the list is taller than the popover. */
const scrollIntoViewIfNeeded = (el: HTMLDivElement | null): void => {
  el?.scrollIntoView?.({ block: 'nearest' });
};

export const mentionOptionId = (listboxId: string, index: number) => `${listboxId}-opt-${index}`;

/** Radix Popover anchored to the caret rectangle the suggestion plugin reports. Focus never leaves the editor. */
export function MentionPopover({
  open,
  anchorRect,
  items,
  activeIndex,
  listboxId,
  loading,
  onSelect,
  onActiveChange,
  onDismiss,
  onEscape,
}: MentionPopoverProps) {
  const { t } = useTranslation();
  const groupLabel: Record<MentionGroup, string> = {
    assistant: t('chat.mentions.groupAssistant', { defaultValue: 'Assistant' }),
    agent: t('chat.mentions.groupAgent', { defaultValue: 'Agent' }),
    people: t('chat.mentions.groupPeople', { defaultValue: 'People in this chat' }),
    teams: t('chat.mentions.groupTeams', { defaultValue: 'Teams' }),
  };
  const kindSuffix = (item: Mentionable): string | null =>
    item.group === 'agent'
      ? item.handle ? `@${item.handle}` : t('chat.mentions.kindAgent', { defaultValue: 'agent' })
      : item.group === 'teams'
        ? t('chat.mentions.kindTeam', { defaultValue: 'team' })
        : null;

  const anchorRef = useMemo(
    () => ({
      current: {
        getBoundingClientRect: () =>
          DOMRect.fromRect({
            x: anchorRect?.left ?? 0,
            y: anchorRect?.top ?? 0,
            width: Math.max(anchorRect?.width ?? 0, 1),
            height: Math.max(anchorRect?.height ?? 0, 1),
          }),
      },
    }),
    [anchorRect],
  );

  if (!anchorRect) return null;

  const rows: React.ReactNode[] = [];
  let lastGroup: MentionGroup | null = null;
  // A heading over a lone assistant or agent row only repeats the row's own name.
  const needsHeading = (group: MentionGroup) =>
    !(group === 'assistant' || group === 'agent') || items.filter((i) => i.group === group).length > 1;
  items.forEach((item, index) => {
    if (item.group !== lastGroup) {
      lastGroup = item.group;
      if (needsHeading(item.group)) {
        rows.push(
          <Box key={`g-${item.group}`} role="presentation" px="2" pt="2" pb="1">
            <Text size="1" weight="medium" color="gray">
              {groupLabel[item.group]}
            </Text>
          </Box>,
        );
      }
    }
    const suffix = kindSuffix(item);
    const selected = index === activeIndex;
    rows.push(
      <div
        key={`${item.ref.type}:${item.ref.id}`}
        id={mentionOptionId(listboxId, index)}
        role="option"
        aria-selected={selected}
        ref={selected ? scrollIntoViewIfNeeded : undefined}
        data-mention-option={`${item.ref.type}:${item.ref.id}`}
        onMouseDown={(e) => e.preventDefault()}
        onMouseMove={() => selected || onActiveChange(index)}
        onClick={() => onSelect(item)}
        style={{
          padding: '6px 8px',
          borderRadius: 'var(--radius-2)',
          cursor: 'pointer',
          backgroundColor: selected ? 'var(--accent-a4)' : 'transparent',
          fontSize: 'var(--font-size-2)',
        }}
      >
        <span>{item.label}</span>
        {suffix ? <span style={{ color: 'var(--slate-11)' }}>{` · ${suffix}`}</span> : null}
      </div>,
    );
  });

  return (
    <Popover.Root open={open} onOpenChange={(next) => next || onDismiss()}>
      {/* A virtual anchor: a fixed-position element would be placed relative to the nearest ancestor with a
          backdrop-filter (the composer card), not the viewport, and the list would open off-screen. */}
      <PopoverPrimitive.Anchor virtualRef={anchorRef} />
      <Popover.Content
        side="top"
        align="start"
        sideOffset={6}
        size="1"
        style={{
          padding: 4,
          minWidth: 220,
          maxWidth: 'min(320px, calc(100vw - 32px))',
          maxHeight: 'min(360px, var(--radix-popover-content-available-height, 360px))',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => {
          e.preventDefault();
          onEscape(e);
        }}
        // Focus stays in the editor, so Radix's outside-focus dismissal would fire at once; the editor's blur and Escape dismiss instead.
        onInteractOutside={(e) => e.preventDefault()}
      >
        {items.length > 0 ? (
          // Only the list scrolls, so the footer tip under it stays in view.
          <div
            id={listboxId}
            role="listbox"
            aria-label={t('chat.mentions.listLabel', { defaultValue: 'Mention suggestions' })}
            aria-activedescendant={mentionOptionId(listboxId, activeIndex)}
            style={{ overflowY: 'auto', minHeight: 0, flex: '1 1 auto' }}
          >
            {rows}
          </div>
        ) : null}
        {items.length === 0 ? (
          <Box px="2" py="2" role="status">
            <Text size="1" color="gray">
              {loading
                ? t('chat.mentions.loading', { defaultValue: 'Searching…' })
                : t('chat.mentions.empty', { defaultValue: 'No matches' })}
            </Text>
          </Box>
        ) : null}
        <ComposerTips />
      </Popover.Content>
    </Popover.Root>
  );
}
