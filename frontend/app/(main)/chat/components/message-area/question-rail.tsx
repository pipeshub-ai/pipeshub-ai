'use client';

import React, { useContext, useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { flushSync } from 'react-dom';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { CHAT_TOP_FADE_HEIGHT_PX, CHAT_TOP_FADE_INSET, ChatFrameTopContext } from './chat-top-bar';

/** The list opens this far above the first tick, so its first row is level with that tick. */
const LIST_LIFT = 'calc((var(--space-1) + var(--space-1) / 2) * -1)';

/** A stop on the rail: the words the chat gave it, and the row it takes the reader to. */
type Landmark = { label: string; el: HTMLElement };

/** A question on a page not loaded yet: the rail lists it above the loaded ones, and a jump loads it. */
export type OlderLandmark = { key: string; label: string };

type Stop = { label: string; el?: HTMLElement; older?: string };

const NO_OLDER: readonly OlderLandmark[] = [];

/** The close waits a beat, so moving from the ticks to the list does not drop it. */
const CLOSE_MS = 160;

/** At the bottom (within this many px) the newest question is the one being read. */
const DEFAULT_PIN_PX = 120;

/**
 * A question as one line, for its stop on the rail (`data-landmark`): no citation marks or markdown, at most 280
 * characters. Pass the question as plain text (mention tokens already resolved). Undefined when nothing is left, so
 * the row is not a stop.
 */
export function questionLandmark(plainQuestion: string): string | undefined {
  const plain = plainQuestion
    .replace(/\s*\[\d{1,2}\]/g, '')
    .replace(/[#*_`>]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 280);
  return plain || undefined;
}

const sameMarks = (marks: readonly Landmark[], nodes: readonly HTMLElement[]) =>
  marks.length === nodes.length && marks.every((m, i) => m.el === nodes[i] && m.label === (nodes[i].dataset.landmark ?? ''));

/** Scrolls `box` (a list that clips) so its child `index` sits in the middle, when the list is taller than the box. */
function centre(box: HTMLElement | null, index: number) {
  const child = box?.children[index] as HTMLElement | undefined;
  if (box && child && box.scrollHeight > box.clientHeight) box.scrollTop = child.offsetTop - (box.clientHeight - child.offsetHeight) / 2;
}

/**
 * The chat's outline: a tick per question on the column's right edge, the one being read marked, and on hover a
 * list of every stop, a click away. Stops are whatever in the scroller carries `data-landmark` (each question, and in
 * a shared chat the unread line), read from the DOM, so nothing threads a list of sections through the chat.
 *
 * Not Radix: the tick rail is a bespoke minimap with no Radix primitive; the rows in its list are Radix Buttons.
 *
 * Keyboard and screen readers: the ticks are buttons named by their question, one Tab stop (the arrow keys, Home and
 * End move between them), and focusing one shows the list with that row marked; Escape hides the list. The list
 * repeats the ticks for the pointer, so it is hidden from screen readers and its rows are out of the Tab order. It
 * opens beside the ticks, never over them, so a press on a tick always lands on that tick.
 *
 * A long chat's older pages may not be in the DOM until loaded: `older` lists their questions first, so the rail
 * counts the whole chat, and `onJumpOlder` loads one and goes there.
 */
export function QuestionRail({
  scrollerRef,
  label,
  onJump,
  older = NO_OLDER,
  onJumpOlder,
  pinPx = DEFAULT_PIN_PX,
  top = `calc(${CHAT_TOP_FADE_INSET} + var(--space-4))`,
  dockHeight = 0,
}: {
  scrollerRef: RefObject<HTMLDivElement | null>;
  /** The rail's accessible name ("Questions in this chat"). */
  label: string;
  /** Takes the chat to a stop's row. */
  onJump: (row: HTMLElement) => void;
  /** Questions above the loaded rows, oldest first. */
  older?: readonly OlderLandmark[];
  /** Loads an older question's page and takes the chat there. */
  onJumpOlder?: (key: string) => void;
  /** Within this distance of the bottom, the newest stop is the one being read. */
  pinPx?: number;
  /**
   * Distance from the frame's top to the first tick, level with where the transcript starts. The default is a column
   * that runs under the top bar: the list's inset below the bar plus the column's own top padding.
   */
  top?: string;
  /** Height of the composer docked over the frame's foot: the ticks and the list end above it. */
  dockHeight?: number;
}) {
  const [marks, setMarks] = useState<readonly Landmark[]>([]);
  const [activeLoaded, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  /** The tick the keyboard is on, while focus is in the rail. */
  const [cursor, setCursor] = useState<number | null>(null);
  /** The list row under the pointer. */
  const [hovered, setHovered] = useState<number | null>(null);
  const ticksRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef(0);
  const frameTop = useContext(ChatFrameTopContext);
  // Where the top bar's fade ends, from the frame's top. The fade is drawn over the column, so the list never opens
  // above this line: a list under it would dissolve with the transcript.
  const clearTop = `calc(${CHAT_TOP_FADE_HEIGHT_PX}px - ${frameTop})`;
  const read = useRef(() => {});
  const pinRef = useRef(pinPx);
  pinRef.current = pinPx;

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return undefined;
    let frame = 0;
    read.current = () => {
      frame = 0;
      const nodes = Array.from(el.querySelectorAll<HTMLElement>('[data-landmark]'));
      setMarks((prev) => (sameMarks(prev, nodes) ? prev : nodes.map((node) => ({ el: node, label: node.dataset.landmark ?? '' }))));
      // The one being read is the last whose top has passed a line a third of the way down: reading happens in the
      // upper middle of a screen, and the very top flips to the next question while its heading is still being read.
      const line = el.getBoundingClientRect().top + el.clientHeight / 3;
      // At the bottom the newest is being read, however short: the last question cannot scroll up to the line when
      // nothing sits below it.
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= pinRef.current;
      let at = 0;
      nodes.forEach((node, i) => {
        if (node.getBoundingClientRect().top <= line) at = i;
      });
      setActive(atBottom ? Math.max(0, nodes.length - 1) : at);
    };
    const soon = () => {
      if (!frame) frame = requestAnimationFrame(() => read.current());
    };
    read.current();
    el.addEventListener('scroll', soon, { passive: true });
    // Rows arriving, older pages, the unread line and a resized column all change the box or its content.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(soon);
    observer?.observe(el);
    if (el.firstElementChild) observer?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener('scroll', soon);
      observer?.disconnect();
      cancelAnimationFrame(frame);
      window.clearTimeout(closeTimer.current);
    };
  }, [scrollerRef]);

  const stops = useMemo<readonly Stop[]>(
    () => [...older.map((o) => ({ label: o.label, older: o.key })), ...marks],
    [older, marks],
  );
  const active = older.length + activeLoaded;
  const go = (stop: Stop) => {
    if (stop.el) onJump(stop.el);
    else if (stop.older) onJumpOlder?.(stop.older);
  };

  // A long chat has more ticks than the column is tall: the one being read stays in sight, in the list too.
  useEffect(() => centre(ticksRef.current, active), [active, stops]);
  useEffect(() => {
    if (open) centre(listRef.current, cursor ?? active);
  }, [open, cursor, active]);

  // One stop is not an outline.
  if (stops.length < 2) return null;

  // The list is level with the first tick, unless that would put its top under the bar's fade: then where it ends.
  const listTop = `max(${LIST_LIFT}, calc(${clearTop} - ${top}))`;

  const show = () => {
    window.clearTimeout(closeTimer.current);
    if (open) return;
    // Drawn before a press that comes at once can land, so a quick click lands on the list as a slow one does.
    flushSync(() => {
      read.current();
      setOpen(true);
    });
  };
  const hide = () => {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(false), CLOSE_MS);
  };
  const focusTick = (i: number) => ticksRef.current?.querySelectorAll('button')[i]?.focus();

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false);
      return;
    }
    const from = cursor ?? active;
    const next =
      e.key === 'ArrowDown' || e.key === 'ArrowRight'
        ? Math.min(from + 1, stops.length - 1)
        : e.key === 'ArrowUp' || e.key === 'ArrowLeft'
          ? Math.max(from - 1, 0)
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? stops.length - 1
              : null;
    if (next === null) return;
    e.preventDefault();
    show();
    focusTick(next);
  };

  return (
    <nav
      aria-label={label}
      data-testid="question-rail"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setCursor(null);
        hide();
      }}
      onKeyDown={onKeyDown}
      style={{
        position: 'absolute',
        right: 'var(--space-3)',
        top,
        // Clear of the docked composer, so a long list never reaches over it.
        bottom: dockHeight > 0 ? dockHeight : 'var(--space-5)',
        zIndex: 8,
        // Only the ticks and the list take the pointer: the strip between them belongs to the chat.
        pointerEvents: 'none',
      }}
    >
      <Flex
        ref={ticksRef}
        direction="column"
        align="end"
        gap="1"
        style={{
          position: 'relative',
          maxHeight: '100%',
          overflow: 'hidden',
          padding: 'var(--space-1) 0 var(--space-1) var(--space-3)',
          pointerEvents: 'auto',
        }}
      >
        {stops.map((m, i) => {
          const on = i === active;
          return (
            <button
              key={`${m.label}-${i}`}
              type="button"
              aria-label={m.label}
              aria-current={on ? 'location' : undefined}
              // One Tab stop: the arrow keys move between ticks.
              tabIndex={i === (cursor ?? active) ? 0 : -1}
              onFocus={() => setCursor(i)}
              onClick={() => go(m)}
              className="focus-ring-inset"
              data-testid="rail-tick"
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                padding: 'var(--space-1) 0',
                margin: 0,
                border: 'none',
                background: 'none',
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              <span
                aria-hidden
                style={{
                  display: 'block',
                  width: on ? 'calc(var(--space-3) + var(--space-1) / 2)' : 'var(--space-2)',
                  height: 'calc(var(--space-1) / 2)',
                  borderRadius: 'calc(var(--space-1) / 4)',
                  // a6, not the a5 of the list rows: a tick alone has to stay visible on the column.
                  backgroundColor: on ? 'var(--gray-a11)' : 'var(--gray-a6)',
                  transition: 'width var(--motion-micro) var(--motion-ease), background-color var(--motion-micro) var(--motion-ease)',
                }}
              />
            </button>
          );
        })}
      </Flex>
      {open && (
        // The floating card every overlay on the column uses, beside the ticks: every stop, the one being read marked.
        // The ticks speak for it to screen readers.
        <Flex
          ref={listRef}
          direction="column"
          aria-hidden
          data-testid="rail-list"
          className="conversation-fade-in no-rise"
          // A press on the card's padding, beside its rows, takes the row at its height.
          onClick={(e) => {
            if (e.target !== e.currentTarget) return;
            const at = Array.from<Element>(e.currentTarget.children).findIndex((row) => {
              const { top: rowTop, bottom } = row.getBoundingClientRect();
              return e.clientY >= rowTop && e.clientY < bottom;
            });
            if (at >= 0) go(stops[at]);
          }}
          style={{
            position: 'absolute',
            top: listTop,
            // Beside the ticks (the widest is 14px), not over them.
            right: 'calc(var(--space-3) + var(--space-1) / 2)',
            width: 280,
            padding: 'var(--space-1)',
            backgroundColor: 'var(--surface-subtle)',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-3)',
            boxShadow: 'var(--shadow-4)',
            // Never past the rail's end, wherever its top landed.
            maxHeight: `min(360px, calc(100% - ${listTop}))`,
            overflowY: 'auto',
            pointerEvents: 'auto',
          }}
        >
          {stops.map((m, i) => {
            const on = i === active;
            const marked = cursor === i;
            return (
              <Button
                key={`${m.label}-${i}`}
                type="button"
                variant="ghost"
                color="gray"
                size="1"
                // The ticks are the keyboard path; the list is for the pointer.
                tabIndex={-1}
                onClick={() => go(m)}
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered((h) => (h === i ? null : h))}
                className="ghost-box"
                data-cursor={marked || undefined}
                style={{
                  justifyContent: 'flex-start',
                  gap: 'var(--space-2)',
                  height: 'auto',
                  minHeight: 'calc(var(--space-6) - var(--space-1) / 2)',
                  padding: 'calc(var(--space-1) + var(--space-1) / 2) var(--space-2)',
                  borderRadius: 'var(--radius-2)',
                  cursor: 'pointer',
                  minWidth: 0,
                  flexShrink: 0,
                  backgroundColor: marked || hovered === i ? 'var(--fill-muted)' : 'transparent',
                  // The keyboard's row is outlined here: its focus is on the tick beside the list.
                  outline: marked ? '2px solid var(--focus-ring)' : undefined,
                  outlineOffset: marked ? -2 : undefined,
                }}
              >
                {/* The tick, repeated in the row: which one you are on reads the same in the list as on the rail. */}
                <Box
                  style={{
                    width: 'calc(var(--space-2) + var(--space-1) / 2)',
                    height: 'calc(var(--space-1) / 2)',
                    flexShrink: 0,
                    borderRadius: 'calc(var(--space-1) / 4)',
                    backgroundColor: on ? 'var(--emerald-9)' : 'var(--gray-a5)',
                  }}
                />
                <Text
                  size="1"
                  truncate
                  weight={on ? 'medium' : 'regular'}
                  style={{ color: on ? 'var(--text-primary)' : 'var(--text-secondary)', minWidth: 0 }}
                >
                  {m.label}
                </Text>
              </Button>
            );
          })}
        </Flex>
      )}
    </nav>
  );
}
