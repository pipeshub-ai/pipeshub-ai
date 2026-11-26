'use client';

import React, { useId } from 'react';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { splitChangedFiles } from '@/lib/harness-jobs/job-files';
import type { ActivityFile, ChatArtifact } from '../../types';
import { VirtualRows, useRowState } from '../virtual-rows';
import { ArtifactCard } from '../message-area/artifacts-panel';
import { previewJobFile } from '../message-area/job-card/preview-job-file';
import { FileRow, estimateFile, fileKey } from './activity-rows';

const TOUCH_HEIGHT = 44;

export interface JobOutputsProps {
  /** What it delivered (`lib/harness-jobs/job-files.ts`), as the answer's file cards draw them. */
  outputs: ReadonlyArray<ChatArtifact>;
  /** Every file it changed, each delivered one marked by the server; null until its activity is read. */
  files: ReadonlyArray<ActivityFile> | null;
  /** Still going: an empty tab says files will show, not that there are none. */
  live: boolean;
  phone: boolean;
  /** Its whole workspace is on Machine; absent for a job with no machine. */
  onMachine?: () => void;
  runId: string;
}

/**
 * A job's Outputs tab (BGUI-11): what it delivered on top, as the answer's own file cards (preview, download), then
 * the files it only worked with, folded into one line, each with how it changed one click down. Delivered and
 * working are the server's one classification, which the report under the card counts too.
 */
export function JobOutputs({ outputs, files, live, phone, onMachine, runId }: JobOutputsProps) {
  const { t } = useTranslation();
  const { working } = splitChangedFiles(files ?? []);
  const [open, setOpen] = useRowState(`outputs.working:${runId}`, false);
  const workingId = useId();
  const empty = outputs.length === 0 && working.length === 0;

  return (
    <Flex direction="column" gap="4" data-testid="job-panel-outputs">
      {empty ? (
        <Text size="2" color="gray" data-testid="job-outputs-empty">
          {live ? t('jobPanel.outputsWillShow') : t('jobPanel.outputsNone')}
        </Text>
      ) : null}
      {outputs.length > 0 ? (
        <Flex direction="column" gap="2" data-testid="job-outputs-delivered">
          <Text size="1" weight="medium" color="gray">
            {t('jobPanel.delivered', { count: outputs.length })}
          </Text>
          {outputs.map((file) => (
            <ArtifactCard key={file.id} artifact={file} onPreview={file.recordId ? previewJobFile : undefined} touch={phone} />
          ))}
        </Flex>
      ) : null}
      {working.length > 0 ? (
        <Box data-testid="job-outputs-working">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={workingId}
            onClick={() => setOpen((v) => !v)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              width: '100%',
              minHeight: phone ? TOUCH_HEIGHT : 32,
              background: 'none',
              border: 0,
              padding: 0,
              cursor: 'pointer',
              color: 'var(--gray-12)',
              font: 'inherit',
              textAlign: 'left',
            }}
          >
            <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={16} color="var(--gray-11)" />
            <Text size="2" weight="medium">
              {t('jobPanel.workingFiles', { count: working.length })}
            </Text>
          </button>
          {open ? (
            <Flex id={workingId} direction="column" gap="2" style={{ marginTop: 'var(--space-2)' }}>
              <Text size="1" color="gray">
                {t('jobPanel.workingFilesHint')}
              </Text>
              <VirtualRows items={working} getKey={fileKey} estimateSize={estimateFile} gap="var(--space-2)" renderRow={(file) => <FileRow file={file} />} />
            </Flex>
          ) : null}
        </Box>
      ) : null}
      {onMachine && !empty ? (
        <Flex>
          <Button size="1" variant="ghost" onClick={onMachine} data-testid="job-outputs-machine" style={phone ? { minHeight: TOUCH_HEIGHT } : undefined}>
            <span aria-hidden="true" style={{ display: 'inline-flex' }}>
              <MaterialIcon name="folder_open" size={14} />
            </span>
            {t('jobPanel.allFilesMachine')}
          </Button>
        </Flex>
      ) : null}
    </Flex>
  );
}
