'use client';

import React, { useId, useState } from 'react';
import { Badge, Box, Flex, Spinner, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ActivityCommand, ActivityFile, ActivityTask } from '../../types';
import { CodeTaskDiff } from '../message-area/code-task-diff';
import type { ActivitySection } from './activity-panel-store';
import { useRowState } from '../virtual-rows';

/** The rows and folds the answer's panel and a job's tabs share. */

function Icon({ name, size = 16, color }: { name: string; size?: number; color?: string }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-flex' }}>
      <MaterialIcon name={name} size={size} color={color} />
    </span>
  );
}

export function Section({
  section,
  title,
  count,
  caption,
  headerRef,
  children,
}: {
  section: ActivitySection;
  title: string;
  count?: number;
  caption?: string;
  headerRef?: React.Ref<HTMLButtonElement>;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const contentId = useId();
  return (
    <Box data-testid={`activity-section-${section}`}>
      <h3 style={{ margin: 0 }}>
        <button
          ref={headerRef}
          type="button"
          aria-expanded={open}
          aria-controls={contentId}
          onClick={() => setOpen((v) => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-2)',
            width: '100%',
            padding: 'var(--space-2) 0',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--gray-12)',
            font: 'inherit',
            fontSize: 'var(--font-size-2)',
            fontWeight: 600,
            textAlign: 'left',
          }}
        >
          <Icon name={open ? 'expand_more' : 'chevron_right'} size={18} color="var(--gray-11)" />
          <span style={{ flex: 1 }}>{title}</span>
          {count !== undefined ? (
            <Text size="1" color="gray" weight="regular">
              {count}
            </Text>
          ) : null}
        </button>
      </h3>
      {open ? (
        <Flex id={contentId} direction="column" gap="2" style={{ paddingBottom: 'var(--space-3)' }}>
          {caption ? (
            <Text size="1" color="gray">
              {caption}
            </Text>
          ) : null}
          {children}
        </Flex>
      ) : null}
    </Box>
  );
}

const TASK_ICON: Record<ActivityTask['status'], { name: string; color: string }> = {
  pending: { name: 'radio_button_unchecked', color: 'var(--gray-11)' },
  in_progress: { name: 'pending', color: 'var(--blue-11)' },
  completed: { name: 'check_circle', color: 'var(--green-11)' },
  cancelled: { name: 'cancel', color: 'var(--gray-11)' },
};

function taskStatusText(t: TFunction, status: ActivityTask['status']): string {
  switch (status) {
    case 'in_progress':
      return t('activityPanel.task.inProgress');
    case 'completed':
      return t('activityPanel.task.completed');
    case 'cancelled':
      return t('activityPanel.task.cancelled');
    default:
      return t('activityPanel.task.pending');
  }
}

export function TaskRow({ task }: { task: ActivityTask }) {
  const { t } = useTranslation();
  const icon = TASK_ICON[task.status];
  return (
    <Flex align="start" gap="2" data-testid="activity-task">
      {task.status === 'in_progress' ? <Spinner size="1" style={{ marginTop: 3 }} /> : <Icon name={icon.name} color={icon.color} />}
      <Text size="2" style={{ textDecoration: task.status === 'cancelled' ? 'line-through' : undefined }}>
        <VisuallyHidden>{`${taskStatusText(t, task.status)}: `}</VisuallyHidden>
        {task.title}
      </Text>
    </Flex>
  );
}

function changeText(t: TFunction, change: ActivityFile['change']): string {
  switch (change) {
    case 'added':
      return t('codeTaskCard.change.added');
    case 'deleted':
      return t('codeTaskCard.change.deleted');
    default:
      return t('codeTaskCard.change.modified');
  }
}

export function FileRow({ file }: { file: ActivityFile }) {
  const { t } = useTranslation();
  const [open, setOpen] = useRowState(`file.open:${file.path}`, false);
  const diffId = useId();
  return (
    <Box data-testid="activity-file">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={diffId}
        // Named by what it shows ("Modified out/report.pdf"); `aria-expanded` says whether its diff is open (F5).
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          width: '100%',
          padding: 'var(--space-1) var(--space-2)',
          background: 'var(--gray-a2)',
          border: '1px solid var(--gray-a5)',
          borderRadius: 'var(--radius-2)',
          cursor: 'pointer',
          color: 'var(--gray-12)',
          font: 'inherit',
          textAlign: 'left',
        }}
      >
        <Badge color={file.change === 'added' ? 'green' : file.change === 'deleted' ? 'red' : 'blue'} variant="soft" highContrast>
          {changeText(t, file.change)}
        </Badge>
        <span style={{ flex: 1, minWidth: 0, fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all' }}>{file.path}</span>
        <Icon name={open ? 'expand_less' : 'expand_more'} color="var(--gray-11)" />
      </button>
      {open ? (
        <Box id={diffId} style={{ marginTop: 'var(--space-2)' }}>
          <CodeTaskDiff file={file} />
        </Box>
      ) : null}
    </Box>
  );
}

export const commandKey = (command: ActivityCommand): string => command.commandId;
export const fileKey = (file: ActivityFile): string => file.path;
export const taskKey = (task: ActivityTask): string => task.taskId;
/** A command block is its header and a terminal of up to 280 px; tens of them already hold thousands of rows. */
export const estimateCommand = (command: ActivityCommand): number => (command.stdout || command.stderr ? 340 : 96);
export const COMMANDS_VIRTUALIZE_AFTER = 12;
export const estimateFile = (): number => 40;
export const estimateTask = (): number => 24;
