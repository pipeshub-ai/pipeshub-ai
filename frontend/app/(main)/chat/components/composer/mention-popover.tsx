'use client';

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Avatar, Box, Popover, Text } from '@radix-ui/themes';
import { Popover as PopoverPrimitive } from 'radix-ui';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
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

const ellipsis: React.CSSProperties = { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' };

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
    agent: t('chat.mentions.groupAgent', { defaultValue: 'Agents' }),
    people: t('chat.mentions.groupPeople', { defaultValue: 'People in this chat' }),
    others: t('chat.mentions.groupOthers', { defaultValue: 'Others in your organization' }),
    teams: t('chat.mentions.groupTeams', { defaultValue: 'Teams' }),
    action: '',
  };
  const kindSuffix = (item: Mentionable): string | null =>
    item.group === 'agent'
      ? item.handle ? `@${item.handle}` : t('chat.mentions.kindAgent', { defaultValue: 'agent' })
      : item.group === 'teams'
        ? t('chat.mentions.kindTeam', { defaultValue: 'team' })
        : null;

  const notInChat = t('chat.mentions.notInChat', { defaultValue: 'Not in this chat' });
  const addPeopleLabel = t('chat.mentions.addPeople', { defaultValue: 'Add people to this chat…' });
  const optionLabel = (item: Mentionable): string | undefined =>
    item.action ? addPeopleLabel : item.group === 'people' || item.group === 'others'
      ? [item.label, item.email, item.inChat === false ? notInChat : null].filter(Boolean).join(', ')
      : undefined;

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
  // A heading over a lone assistant row only repeats the row's own name; agents always get theirs.
  const needsHeading = (group: MentionGroup) => group !== 'action' && (group !== 'assistant' || items.filter((i) => i.group === group).length > 1);
  const noMatches = items.length > 0 && items.every((i) => i.action);
  const hasDisabledAgent = items.some((i) => i.disabled);
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
        key={item.action ?? `${item.ref.type}:${item.ref.id}`}
        id={mentionOptionId(listboxId, index)}
        role="option"
        aria-selected={selected}
        aria-disabled={item.disabled || undefined}
        aria-label={optionLabel(item)}
        ref={selected ? scrollIntoViewIfNeeded : undefined}
        data-mention-option={item.action ?? `${item.ref.type}:${item.ref.id}`}
        onMouseDown={(e) => e.preventDefault()}
        onMouseMove={() => selected || onActiveChange(index)}
        onClick={() => (item.disabled ? undefined : onSelect(item))}
        style={{
          display: item.group === 'agent' || item.group === 'people' || item.group === 'others' || item.action ? 'flex' : undefined,
          ...(item.action ? { borderTop: '1px solid var(--slate-5)', borderRadius: 0, marginTop: 4, paddingTop: 8 } : {}),
          alignItems: 'center',
          gap: 8,
          opacity: item.disabled ? 0.5 : 1,
          padding: '6px 8px',
          borderRadius: 'var(--radius-2)',
          cursor: item.disabled ? 'not-allowed' : 'pointer',
          backgroundColor: selected ? 'var(--accent-a4)' : 'transparent',
          fontSize: 'var(--font-size-2)',
        }}
      >
        {item.action ? (
          <>
            <span aria-hidden="true" style={{ display: 'inline-flex' }}>
              <MaterialIcon name="person_add" size={16} color="var(--slate-11)" />
            </span>
            <span style={ellipsis}>{addPeopleLabel}</span>
          </>
        ) : null}
        {item.group === 'agent' ? (
          <span data-testid="mention-agent-avatar" style={{ display: 'inline-flex', flexShrink: 0 }}>
            <Avatar size="1" radius="full" variant="soft" color="jade" fallback={Array.from(item.label.trim())[0]?.toUpperCase() ?? '?'} />
          </span>
        ) : null}
        {item.action ? null : item.group === 'people' || item.group === 'others' ? (
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span data-testid="mention-label" style={ellipsis}>
                {item.label}
              </span>
              {item.inChat === false ? (
                <span
                  data-testid="mention-not-in-chat"
                  style={{
                    flexShrink: 0,
                    fontSize: 'var(--font-size-1)',
                    color: 'var(--slate-11)',
                    border: '1px solid var(--slate-7)',
                    borderRadius: 'var(--radius-2)',
                    padding: '0 6px',
                    lineHeight: '18px',
                  }}
                >
                  {notInChat}
                </span>
              ) : null}
            </div>
            {item.email ? (
              <span data-testid="mention-email" style={{ ...ellipsis, color: 'var(--slate-11)', fontSize: 'var(--font-size-1)' }}>
                {item.email}
              </span>
            ) : null}
          </div>
        ) : (
          <>
            <span data-testid="mention-label" style={item.group === 'agent' ? ellipsis : undefined}>
              {item.label}
            </span>
            {suffix ? (
              <span data-testid="mention-suffix" style={{ color: 'var(--slate-11)', ...(item.group === 'agent' ? { flexShrink: 0 } : {}) }}>
                {item.group === 'agent' ? suffix : ` · ${suffix}`}
              </span>
            ) : null}
          </>
        )}
      </div>,
    );
  });

  const emptyStatus = (
    <Box px="2" py="2" role="status">
      <Text size="1" color="gray">
        {loading
          ? t('chat.mentions.loading', { defaultValue: 'Searching…' })
          : t('chat.mentions.empty', { defaultValue: 'No matches' })}
      </Text>
    </Box>
  );

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
        {items.length === 0 ? (
          emptyStatus
        ) : (
          // Only the list scrolls, so the footer tip under it stays in view.
          <div
            id={listboxId}
            role="listbox"
            aria-label={t('chat.mentions.listLabel', { defaultValue: 'Mention suggestions' })}
            aria-activedescendant={activeIndex >= 0 ? mentionOptionId(listboxId, activeIndex) : undefined}
            style={{ overflowY: 'auto', minHeight: 0, flex: '1 1 auto' }}
          >
            {noMatches ? emptyStatus : null}
            {rows}
          </div>
        )}
        {hasDisabledAgent ? (
          <Text as="div" size="1" role="note" data-testid="mention-one-agent-hint" style={{ color: 'var(--slate-11)', padding: '6px 8px 2px' }}>
            {t('chat.mentions.oneAgentHint')}
          </Text>
        ) : null}
        <ComposerTips />
      </Popover.Content>
    </Popover.Root>
  );
}
