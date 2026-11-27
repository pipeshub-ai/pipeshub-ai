'use client';

import React, { useMemo, useState } from 'react';
import { Box, Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { ActivityStream } from '../../types';
import { useFollowTail } from './use-follow-tail';
import { windowRows } from './windowed-rows';

export interface TerminalLine {
  text: string;
  stream: ActivityStream;
}

/** A terminal is dark in both themes: that is how people recognise one. Contrast is at least 7:1 for text. */
export const TERMINAL_COLORS = {
  surface: '#16181a',
  border: '#2e3230',
  text: '#e6e8e4',
  stderr: '#ffb4a8',
  stderrRule: '#e5484d',
  muted: '#a5aba3',
} as const;

const ROW_HEIGHT_PX = 18;

function split(text: string): string[] {
  if (!text) return [];
  const lines = text.split('\n');
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
}

/** Stdout then stderr: the server keeps each stream's tail apart, so their interleaving is not known. */
export function terminalLines(stdout: string, stderr: string): TerminalLine[] {
  return [
    ...split(stdout).map((text) => ({ text, stream: 'stdout' as const })),
    ...split(stderr).map((text) => ({ text, stream: 'stderr' as const })),
  ];
}

export interface TerminalOutputProps {
  lines: TerminalLine[];
  /** The accessible name of the (focusable) output block. */
  label: string;
  maxHeight?: number;
}

/**
 * A command's output, windowed (FE-5) and following its end unless the reader
 * scrolled up (FE-3). Text only: output is never rendered as markup.
 */
export function TerminalOutput({ lines, label, maxHeight = 280 }: TerminalOutputProps) {
  const { t } = useTranslation();
  const last = lines[lines.length - 1];
  const contentKey = `${lines.length}:${last?.text.length ?? 0}`;
  const { ref, onScroll, following, hasNew, jumpToLatest } = useFollowTail<HTMLDivElement>(contentKey);
  const [scrollTop, setScrollTop] = useState(0);
  const height = Math.min(maxHeight, Math.max(1, lines.length) * ROW_HEIGHT_PX);
  const rows = useMemo(
    () => windowRows(lines.length, ROW_HEIGHT_PX, height, following ? Number.MAX_SAFE_INTEGER : scrollTop),
    [lines.length, height, following, scrollTop],
  );

  return (
    <Box style={{ position: 'relative' }}>
      <div
        ref={ref}
        role="group"
        aria-label={label}
        tabIndex={0}
        data-testid="activity-output"
        onScroll={(event) => {
          onScroll();
          setScrollTop(event.currentTarget.scrollTop);
        }}
        style={{
          height,
          overflow: 'auto',
          backgroundColor: TERMINAL_COLORS.surface,
          color: TERMINAL_COLORS.text,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          fontSize: 12,
          lineHeight: `${ROW_HEIGHT_PX}px`,
          borderRadius: 'var(--radius-2)',
          outlineOffset: 2,
        }}
      >
        <div style={{ height: rows.padTop }} />
        {lines.slice(rows.start, rows.end).map((line, index) => (
          <div
            key={rows.start + index}
            data-stream={line.stream}
            style={{
              height: ROW_HEIGHT_PX,
              whiteSpace: 'pre',
              padding: '0 var(--space-2)',
              color: line.stream === 'stderr' ? TERMINAL_COLORS.stderr : TERMINAL_COLORS.text,
              borderLeft: `2px solid ${line.stream === 'stderr' ? TERMINAL_COLORS.stderrRule : 'transparent'}`,
            }}
          >
            {line.text || ' '}
          </div>
        ))}
        <div style={{ height: rows.padBottom }} />
      </div>
      {hasNew && !following ? (
        <Button
          size="1"
          variant="solid"
          onClick={jumpToLatest}
          style={{ position: 'absolute', right: 'var(--space-2)', bottom: 'var(--space-2)' }}
        >
          {t('activityPanel.jumpToLatest')}
        </Button>
      ) : null}
    </Box>
  );
}
