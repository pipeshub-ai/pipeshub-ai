'use client';

import React, { useId, useMemo } from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { formatFileSize } from '@/app/components/file-preview/utils';
import type { CodeTaskFile } from '../../types';

type DiffLineKind = 'added' | 'removed' | 'hunk' | 'header' | 'context';

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

/** Lines of a unified diff, classified by their prefix (which stays in the text, so colour is never the only cue). */
export function diffLines(diff: string): DiffLine[] {
  const lines = diff.endsWith('\n') ? diff.slice(0, -1).split('\n') : diff.split('\n');
  return lines.map((text) => {
    if (text.startsWith('+++') || text.startsWith('---')) return { kind: 'header', text };
    if (text.startsWith('@@')) return { kind: 'hunk', text };
    if (text.startsWith('+')) return { kind: 'added', text };
    if (text.startsWith('-')) return { kind: 'removed', text };
    return { kind: 'context', text };
  });
}

const LINE_STYLE: Record<DiffLineKind, React.CSSProperties> = {
  added: { backgroundColor: 'var(--green-3)', color: 'var(--green-11)' },
  removed: { backgroundColor: 'var(--red-3)', color: 'var(--red-11)' },
  hunk: { color: 'var(--blue-11)' },
  header: { color: 'var(--olive-11)', fontWeight: 600 },
  context: {},
};

export interface CodeTaskDiffProps {
  file: CodeTaskFile;
}

/**
 * One changed file's diff as text. A summary of the line counts is its text
 * alternative; binary and oversized files say so instead of showing bytes.
 */
export function CodeTaskDiff({ file }: CodeTaskDiffProps) {
  const { t } = useTranslation();
  const summaryId = useId();
  const lines = useMemo(() => (file.diff ? diffLines(file.diff) : []), [file.diff]);
  const size = formatFileSize(file.sizeBytes ?? 0);

  if (file.binary) {
    return <Text size="1" color="gray">{t('codeTaskCard.binary', { size })}</Text>;
  }
  if (file.tooLarge) {
    return <Text size="1" color="gray">{t('codeTaskCard.tooLarge', { size })}</Text>;
  }
  if (!file.diff) {
    return <Text size="1" color="gray">{t('codeTaskCard.noDiff')}</Text>;
  }
  const added = lines.filter((line) => line.kind === 'added').length;
  const removed = lines.filter((line) => line.kind === 'removed').length;

  return (
    <Box>
      <Text as="p" id={summaryId} size="1" color="gray" style={{ margin: '0 0 var(--space-1)' }}>
        {t('codeTaskCard.diffSummary', { added, removed })}
        {file.diffTruncated ? ` ${t('codeTaskCard.diffTruncated')}` : ''}
      </Text>
      <Box
        asChild
        style={{
          margin: 0,
          padding: 'var(--space-2)',
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-5)',
          maxHeight: '320px',
          overflow: 'auto',
          fontFamily: 'monospace',
          fontSize: '12px',
          lineHeight: 1.5,
        }}
      >
        <pre
          role="region"
          tabIndex={0}
          aria-label={t('codeTaskCard.diffLabel', { path: file.path })}
          aria-describedby={summaryId}
          data-testid="code-task-diff"
        >
          {lines.map((line, index) => (
            <span
              key={index}
              data-kind={line.kind}
              style={{ display: 'block', whiteSpace: 'pre', minWidth: 'fit-content', ...LINE_STYLE[line.kind] }}
            >
              {line.text === '' ? ' ' : line.text}
            </span>
          ))}
        </pre>
      </Box>
    </Box>
  );
}
