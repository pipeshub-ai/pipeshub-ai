'use client';

import React, { useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { formatFileSize } from '@/app/components/file-preview/utils';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ChangedFile } from '../../engine-steps';
import { CodeTaskDiff } from './code-task-diff';

/** How much of a diff the answer shows; the whole of it is in "View activity". */
const DIFF_LINES = 200;

const CHANGE_COLOR: Record<ChangedFile['change'], string> = {
  added: 'var(--green-11)',
  modified: 'var(--amber-11)',
  deleted: 'var(--red-11)',
};

function shownDiff(diff: string): { diff: string; cut: boolean } {
  const lines = diff.split('\n');
  return lines.length > DIFF_LINES ? { diff: lines.slice(0, DIFF_LINES).join('\n'), cut: true } : { diff, cut: false };
}

/**
 * The files a run made or changed, one line each (CTL-4), for engine and loop runs alike. A file with a diff opens
 * it inline, one at a time; a new file without one (a loop run's output) opens its preview.
 */
export function FilesChanged({ files, onOpen }: { files: ChangedFile[]; onOpen?: (file: ChangedFile) => void }) {
  const { t } = useTranslation();
  const [openPath, setOpenPath] = useState<string | null>(null);
  if (files.length === 0) return null;
  const title = t('agentActivity.files.title');
  return (
    <Box style={{ marginTop: 'var(--space-2)' }}>
      <Text as="p" size="1" weight="medium" style={{ color: 'var(--slate-11)', margin: '0 0 var(--space-1)' }}>
        {title}
      </Text>
      <ul aria-label={title} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {files.map((file) => {
          const previews = !file.diff && file.artifactId !== undefined && onOpen !== undefined;
          const open = openPath === file.path;
          const shown = open && file.diff ? shownDiff(file.diff) : null;
          return (
            <li key={file.path} style={{ padding: 'var(--space-1) 0' }}>
              <Flex
                asChild
                align="center"
                gap="2"
                style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', width: '100%', textAlign: 'left', minHeight: '28px' }}
              >
                <button
                  type="button"
                  aria-expanded={previews ? undefined : open}
                  onClick={() => (previews ? onOpen(file) : setOpenPath(open ? null : file.path))}
                >
                  <MaterialIcon name={previews ? 'visibility' : open ? 'expand_less' : 'expand_more'} size={14} color="var(--slate-11)" />
                  <Text size="1" style={{ fontFamily: 'monospace', wordBreak: 'break-all', color: 'var(--slate-12)' }}>
                    {file.path}
                  </Text>
                  <Text size="1" style={{ color: CHANGE_COLOR[file.change] }}>
                    {t(`agentActivity.files.${file.change}`)}
                  </Text>
                  {file.diff ? (
                    <Text size="1" color="gray">
                      <span style={{ color: 'var(--green-11)' }}>+{file.additions}</span>{' '}
                      <span style={{ color: 'var(--red-11)' }}>−{file.deletions}</span>
                    </Text>
                  ) : file.sizeBytes !== undefined ? (
                    <Text size="1" color="gray">
                      {formatFileSize(file.sizeBytes)}
                    </Text>
                  ) : null}
                </button>
              </Flex>
              {open ? (
                <Box style={{ marginTop: 'var(--space-1)' }}>
                  <CodeTaskDiff file={shown ? { ...file, diff: shown.diff, diffTruncated: false } : file} />
                  {shown && (shown.cut || file.diffTruncated) ? (
                    <Text size="1" color="gray">
                      {t('agentActivity.files.fullDiff')}
                    </Text>
                  ) : null}
                </Box>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Box>
  );
}
