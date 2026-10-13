'use client';

import React, { forwardRef, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Box, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MentionIcon } from '@/chat/components/mention-icon';
import { MentionMenu, type MentionMenuHandle } from '@/chat/components/mention-menu';
import type { ChatKnowledgeFilters } from '@/chat/types';
import {
  activeMentionQuery,
  caretAfterMention,
  chipDeletionRange,
  insertMention,
  locateMentions,
  mentionChipBounds,
  mentionChipRemoval,
  mentionLabel,
  retainedMentions,
  withUniqueLabel,
  type ActiveMention,
  type MentionNode,
} from '@/chat/utils/node-mentions';

const MENU_MAX_HEIGHT = 340;

interface Box2D {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface HoveredChip {
  id: string;
  start: number;
  name: string;
  label: string;
  /** The line of the chip under the pointer, and the chip's last line, where its remove button sits. */
  line: Box2D;
  last: Box2D;
  text: string;
}

export interface MentionParent {
  nodeType: string;
  id: string;
}

interface MentionComposerFieldProps {
  value: string;
  mentions: readonly MentionNode[];
  onChange: (value: string) => void;
  onMentionsChange: (mentions: MentionNode[]) => void;
  onKeyDown?: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onFocus?: () => void;
  onBlur?: () => void;
  onInput?: (event: React.FormEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  readOnly?: boolean;
  multiline?: boolean;
  /** Search under this node. Omitted, `@` searches every node the user can reach. */
  parent?: MentionParent | null;
  /** The chat's scope: with it, the menu's checkboxes tick nodes into it. */
  selection?: ChatKnowledgeFilters;
  onSelectionChange?: (next: ChatKnowledgeFilters) => void;
  style?: React.CSSProperties;
  'aria-label'?: string;
}

export const MentionComposerField = forwardRef<HTMLTextAreaElement, MentionComposerFieldProps>(
  function MentionComposerField(
    {
      value,
      mentions,
      onChange,
      onMentionsChange,
      onKeyDown,
      onFocus,
      onBlur,
      onInput,
      placeholder,
      readOnly,
      multiline = false,
      parent,
      selection,
      onSelectionChange,
      style,
      'aria-label': ariaLabel,
    },
    ref,
  ) {
    const innerRef = useRef<HTMLTextAreaElement | null>(null);
    const backdropRef = useRef<HTMLDivElement | null>(null);
    const removeRef = useRef<HTMLButtonElement | null>(null);
    const { t } = useTranslation();
    const menuRef = useRef<MentionMenuHandle | null>(null);
    // A dismissed `@query` stays closed while the user types on after it.
    const [dismissed, setDismissed] = useState<ActiveMention | null>(null);
    // Kept with the text it was measured against, so any change to the text hides it.
    const [hovered, setHovered] = useState<HoveredChip | null>(null);
    const [menuBox, setMenuBox] = useState<{ left: number; width: number; bottom: number; maxHeight: number } | null>(null);
    const [caret, setCaret] = useState(0);
    // Ticking scopes the chat; the `@query` ticked from is then not part of the message.
    // Held as it was at the tick: words typed after it, which the query runs on into, are the user's.
    const tickedTokenRef = useRef<ActiveMention | null>(null);
    const valueRef = useRef(value);
    useLayoutEffect(() => {
      valueRef.current = value;
    }, [value]);
    const listId = useId();
    const [activeOption, setActiveOption] = useState<string | undefined>(undefined);
    const onActiveChange = useCallback((id: string | undefined) => setActiveOption(id), []);

    const setRefs = (node: HTMLTextAreaElement | null) => {
      innerRef.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) ref.current = node;
    };

    const active = readOnly ? null : activeMentionQuery(value, caret);
    const dismissedIntact =
      dismissed !== null &&
      value.slice(dismissed.start, dismissed.start + dismissed.query.length + 1) === `@${dismissed.query}`;
    const menuOpen =
      active !== null &&
      !(dismissed && dismissedIntact && dismissed.start === active.start && active.query.startsWith(dismissed.query));
    useEffect(() => {
      if (dismissed && !dismissedIntact) setDismissed(null);
    }, [dismissed, dismissedIntact]);
    const spans = useMemo(() => locateMentions(value, mentions), [value, mentions]);

    // However the menu closes after a tick (Escape, a click elsewhere, no more matches), the `@query` goes.
    useEffect(() => {
      if (menuOpen) return;
      const token = tickedTokenRef.current;
      tickedTokenRef.current = null;
      if (!token) return;
      const text = valueRef.current;
      const typed = `@${token.query}`;
      if (text.slice(token.start, token.start + typed.length) !== typed) return;
      let end = token.start + typed.length;
      if (end < text.length && !/\s/.test(text[end] ?? '')) return;
      // Take one following space with it, so no double space is left behind.
      if ((token.start === 0 || /\s/.test(text[token.start - 1] ?? '')) && text[end] === ' ') end += 1;
      onChange(text.slice(0, token.start) + text.slice(end));
      setDismissed(null);
      const field = innerRef.current;
      const position = field?.selectionStart ?? text.length;
      const next = position > token.start ? Math.max(token.start, position - (end - token.start)) : position;
      setCaret(next);
      if (field && document.activeElement === field) {
        requestAnimationFrame(() => field.setSelectionRange(next, next));
      }
    }, [menuOpen, onChange]);

    const placeMenu = () => {
      const el = innerRef.current;
      if (!el) return;
      const shell = el.closest('[data-composer-shell]') ?? el;
      const rect = shell.getBoundingClientRect();
      setMenuBox({
        left: rect.left,
        width: rect.width,
        bottom: window.innerHeight - rect.top + 8,
        maxHeight: Math.max(160, Math.min(MENU_MAX_HEIGHT, rect.top - 16)),
      });
    };

    // The composer moves without scrolling or resizing (a chip added, content below it changing),
    // so the menu follows it frame by frame while open; placed before paint so it never shows where it last was.
    useLayoutEffect(() => {
      if (!menuOpen) return;
      let frame = 0;
      let last = '';
      const follow = () => {
        const el = innerRef.current;
        const rect = (el?.closest('[data-composer-shell]') ?? el)?.getBoundingClientRect();
        const key = rect ? `${rect.left}|${rect.top}|${rect.width}|${window.innerHeight}` : '';
        if (key !== last) {
          last = key;
          placeMenu();
        }
        frame = requestAnimationFrame(follow);
      };
      follow();
      return () => cancelAnimationFrame(frame);
    }, [menuOpen]);

    const replaceToken = (keep: string) => {
      const field = innerRef.current;
      const cursor = field?.selectionStart ?? value.length;
      const token = activeMentionQuery(value, cursor);
      if (!token) return;
      onChange(`${value.slice(0, token.start)}${keep}${value.slice(cursor)}`);
      const pos = token.start + keep.length;
      setCaret(pos);
      if (keep) setDismissed(null);
      requestAnimationFrame(() => {
        field?.focus();
        field?.setSelectionRange(pos, pos);
      });
    };

    const closeMenu = () => setDismissed(active);
    // Names have spaces, so a query runs on past one; once it has a space and matches nothing, the user is writing on.
    const onNoMatches = () => {
      if (active && /\s/.test(active.query)) closeMenu();
    };

    const pick = (mention: MentionNode) => {
      const field = innerRef.current;
      const cursor = field?.selectionStart ?? value.length;
      const token = activeMentionQuery(value, cursor);
      if (!token) return;
      const node = withUniqueLabel(mention, mentions);
      const next = insertMention(value, token.start, cursor, node);
      onChange(next);
      onMentionsChange(locateMentions(next, [...mentions, node]).map((span) => span.node));
      setDismissed(null);
      const pos = caretAfterMention(token.start, node);
      requestAnimationFrame(() => {
        field?.focus();
        field?.setSelectionRange(pos, pos);
      });
    };

    // Cuts `[start, end)` out of the text, keeping the caret where it was relative to what is left.
    const removeRange = (start: number, end: number, caretAt: number) => {
      const next = value.slice(0, start) + value.slice(end);
      onChange(next);
      onMentionsChange(retainedMentions(next, mentions));
      setHovered(null);
      const pos = caretAt >= end ? caretAt - (end - start) : Math.min(caretAt, start);
      setCaret(pos);
      const field = innerRef.current;
      if (field && document.activeElement === field) {
        requestAnimationFrame(() => field.setSelectionRange(pos, pos));
      }
    };

    const removeChip = (id: string, start: number) => {
      const span = locateMentions(value, mentions).find((candidate) => candidate.node.id === id && candidate.start === start);
      if (!span) return;
      const range = mentionChipRemoval(value, span);
      removeRange(range.start, range.end, innerRef.current?.selectionStart ?? range.start);
    };

    const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (menuOpen) {
        const at = event.currentTarget.selectionStart ?? value.length;
        const caretAtEnd = at >= value.length || /\s/.test(value[at] ?? '');
        if (menuRef.current?.keyDown(event, caretAtEnd)) return;
        if (event.key === 'Escape') {
          event.preventDefault();
          closeMenu();
          return;
        }
      }
      if (!readOnly && (event.key === 'Backspace' || event.key === 'Delete')) {
        const from = event.currentTarget.selectionStart ?? value.length;
        const to = event.currentTarget.selectionEnd ?? from;
        const range = chipDeletionRange(value, mentions, from, to, event.key);
        if (range) {
          event.preventDefault();
          removeRange(range.start, range.end, range.start);
          return;
        }
      }
      onKeyDown?.(event);
    };

    const syncScroll = () => {
      const field = innerRef.current;
      const backdrop = backdropRef.current;
      if (!field || !backdrop) return;
      backdrop.scrollTop = field.scrollTop;
      backdrop.scrollLeft = field.scrollLeft;
      setHovered(null);
    };

    // The textarea covers the chips, so which one the pointer is over is worked out from the backdrop.
    const trackHover = (event: React.MouseEvent) => {
      const backdrop = backdropRef.current;
      const box = backdrop?.parentElement?.getBoundingClientRect();
      if (!backdrop || !box) return;
      const local = (rect: DOMRect): Box2D => ({
        left: rect.left - box.left,
        top: rect.top - box.top,
        width: rect.width,
        height: rect.height,
      });
      for (const chip of backdrop.querySelectorAll<HTMLElement>('[data-mention-id]')) {
        const rects = Array.from(chip.getClientRects());
        const line = rects.find(
          (rect) =>
            event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom,
        );
        const last = rects[rects.length - 1];
        if (!line || !last) continue;
        const next: HoveredChip = {
          id: chip.dataset.mentionId ?? '',
          start: Number(chip.dataset.mentionStart),
          name: chip.dataset.mentionName ?? '',
          label: chip.dataset.mentionLabel ?? '',
          line: local(line),
          last: local(last),
          text: value,
        };
        setHovered((prev) =>
          prev &&
          prev.id === next.id &&
          prev.start === next.start &&
          prev.line.left === next.line.left &&
          prev.line.top === next.line.top &&
          prev.text === value
            ? prev
            : next,
        );
        return;
      }
      setHovered(null);
    };

    const textStyle: React.CSSProperties = {
      fontSize: style?.fontSize ?? 'var(--font-size-2)',
      lineHeight: style?.lineHeight ?? (multiline ? 1.5 : '20px'),
      fontFamily: style?.fontFamily ?? 'Manrope, sans-serif',
      // A textarea does not inherit these and the backdrop does; they must match glyph for glyph.
      fontWeight: style?.fontWeight ?? 400,
      letterSpacing: style?.letterSpacing ?? 'normal',
      whiteSpace: multiline ? 'pre-wrap' : 'pre',
      overflowWrap: 'break-word',
      wordBreak: 'break-word',
      padding: 0,
      margin: 0,
    };

    let cursor = 0;
    const highlighted: React.ReactNode[] = [];
    spans.forEach((span) => {
      const chip = mentionChipBounds(value, span);
      if (chip.start > cursor) highlighted.push(value.slice(cursor, chip.start));
      highlighted.push(
        <span
          key={`${span.node.id}-${span.start}`}
          className="kb-mention-token"
          data-mention-id={span.node.id}
          data-mention-start={span.start}
          data-mention-name={span.node.name}
          data-mention-label={mentionLabel(span.node)}
        >
          {chip.start < span.start ? (
            <span className="kb-mention-lead">
              {value.slice(chip.start, span.start)}
              <span className="kb-mention-icon">
                <MentionIcon
                  nodeType={span.node.nodeType}
                  connector={span.node.connector}
                  extension={span.node.extension}
                  mimeType={span.node.mimeType}
                  name={span.node.name}
                />
              </span>
            </span>
          ) : null}
          {value.slice(span.start, chip.end)}
        </span>,
      );
      cursor = chip.end;
    });
    if (cursor < value.length) highlighted.push(value.slice(cursor));

    const menu = menuOpen && menuBox && active
      ? createPortal(
          <Box
            data-composer-popover=""
            onMouseDown={(event) => event.preventDefault()}
            style={{
              position: 'fixed',
              left: menuBox.left,
              width: menuBox.width,
              bottom: menuBox.bottom,
              zIndex: 80,
              background: 'var(--olive-1)',
              border: '1px solid var(--olive-4)',
              borderRadius: 'var(--radius-3)',
              boxShadow: 'var(--shadow-5)',
              padding: 6,
              boxSizing: 'border-box',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <MentionMenu
              ref={menuRef}
              query={active.query}
              parent={parent}
              selection={selection}
              onSelectionChange={
                onSelectionChange
                  ? (next) => {
                      if (active) tickedTokenRef.current = active;
                      onSelectionChange(next);
                    }
                  : undefined
              }
              onPick={pick}
              onNoMatches={onNoMatches}
              onClearQuery={() => replaceToken('@')}
              maxHeight={menuBox.maxHeight - 14}
              listId={listId}
              onActiveChange={onActiveChange}
            />
          </Box>,
          // Inside the theme root, so Radix tokens and components (the checkboxes) are styled.
          document.querySelector('.radix-themes') ?? document.body,
        )
      : null;

    return (
      <div style={{ position: 'relative', flex: 1, minWidth: 0, width: multiline ? '100%' : undefined }}>
        {menu}
        {hovered && hovered.text === value && hovered.label !== hovered.name && (
          <Tooltip key={`${hovered.id}|${hovered.line.left}|${hovered.line.top}`} content={hovered.name} open side="top">
            <span aria-hidden style={{ position: 'absolute', ...hovered.line, pointerEvents: 'none' }} />
          </Tooltip>
        )}
        {hovered && hovered.text === value && !readOnly && (
          <button
            ref={removeRef}
            type="button"
            tabIndex={-1}
            className="kb-mention-remove"
            aria-label={t('chat.mentionRemove', { name: hovered.name, defaultValue: 'Remove {{name}}' })}
            // Keeps focus, and the caret, in the textarea.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => removeChip(hovered.id, hovered.start)}
            onMouseLeave={(event) => {
              if (event.relatedTarget !== innerRef.current) setHovered(null);
            }}
            style={{
              left: hovered.last.left + hovered.last.width - 18,
              top: hovered.last.top + (hovered.last.height - 16) / 2,
            }}
          >
            <MaterialIcon name="close" size={12} color="currentColor" />
          </button>
        )}
        {!value && placeholder && !readOnly && (
          <span className="chat-placeholder-shimmer" aria-hidden="true">
            {placeholder}
          </span>
        )}
        {value && (
          <div
            ref={backdropRef}
            aria-hidden
            style={{
              ...textStyle,
              position: 'absolute',
              inset: 0,
              overflow: 'hidden',
              pointerEvents: 'none',
              color: readOnly ? (typeof style?.color === 'string' ? style.color : 'var(--slate-a8)') : 'var(--slate-12)',
            }}
          >
            {highlighted}
          </div>
        )}
        <textarea
          ref={setRefs}
          className="chat-input-textarea"
          value={value}
          readOnly={readOnly}
          rows={1}
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-controls={menuOpen ? listId : undefined}
          aria-activedescendant={menuOpen ? activeOption : undefined}
          placeholder={placeholder}
          onChange={(event) => {
            onChange(event.target.value);
            setCaret(event.target.selectionStart ?? event.target.value.length);
          }}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? value.length)}
          onKeyDown={handleKeyDown}
          onKeyUp={(event) => {
            setCaret(event.currentTarget.selectionStart ?? value.length);
            placeMenu();
          }}
          onClick={(event) => {
            setCaret(event.currentTarget.selectionStart ?? value.length);
            placeMenu();
          }}
          onFocus={onFocus}
          onBlur={() => {
            if (menuOpen) closeMenu();
            onBlur?.();
          }}
          onScroll={syncScroll}
          onMouseMove={trackHover}
          onMouseLeave={(event) => {
            if (!(event.relatedTarget instanceof Node && removeRef.current?.contains(event.relatedTarget))) setHovered(null);
          }}
          onInput={onInput}
          style={{
            ...style,
            ...textStyle,
            width: '100%',
            display: 'block',
            border: 'none',
            outline: 'none',
            resize: 'none',
            background: 'transparent',
            position: 'relative',
            color: value ? 'transparent' : (readOnly ? style?.color : 'var(--slate-12)'),
            WebkitTextFillColor: value ? 'transparent' : undefined,
            caretColor: 'var(--slate-12)',
          }}
        />
      </div>
    );
  },
);
