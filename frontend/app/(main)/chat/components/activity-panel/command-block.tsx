'use client';

import React, { useMemo, useState } from 'react';
import { Badge, Box, Button, Flex, Spinner, Text, Theme } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { formatBytes, formatDuration } from '../../activity';
import { skillReadLabel } from '../../utils/tool-display';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ActivityCommand } from '../../types';
import { TERMINAL_COLORS, TerminalOutput, terminalLines } from './terminal-output';

function StatusBadge({ command }: { command: ActivityCommand }) {
  const { t } = useTranslation();
  switch (command.status) {
    case 'running':
      return (
        <Badge color="blue" variant="soft">
          <Spinner size="1" />
          {t('activityPanel.command.running')}
        </Badge>
      );
    case 'timed_out':
      return <Badge color="amber" variant="soft" highContrast>{t('activityPanel.command.timedOut')}</Badge>;
    case 'cancelled':
      return <Badge color="gray" variant="soft">{t('activityPanel.command.cancelled')}</Badge>;
    default:
      if (command.exitCode === null) {
        return (
          <Badge color={command.status === 'failed' ? 'red' : 'green'} variant="soft">
            {command.status === 'failed' ? t('activityPanel.command.failed') : t('activityPanel.command.done')}
          </Badge>
        );
      }
      return (
        <Badge color={command.exitCode === 0 ? 'green' : 'red'} variant="soft">
          {t('activityPanel.command.exit', { code: command.exitCode })}
        </Badge>
      );
  }
}

/**
 * One command; a command that read a skill (FIX-SKILLSHOW) is a plain, closed step naming the skill ("Read the PDF
 * skill"), its terminal block one click away.
 */
export function CommandBlock({ command, onOpenLog }: { command: ActivityCommand; onOpenLog: () => void }) {
  const [open, setOpen] = useState(false);
  if (!command.skills) return <TerminalBlock command={command} onOpenLog={onOpenLog} />;
  return (
    <Box data-testid="activity-skill-command" data-status={command.status}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-1)',
          width: '100%',
          minHeight: 32,
          padding: 0,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--gray-12)',
          font: 'inherit',
          fontSize: 'var(--font-size-1)',
          textAlign: 'left',
        }}
      >
        <MaterialIcon name={open ? 'expand_more' : 'chevron_right'} size={14} color="var(--gray-11)" />
        <MaterialIcon name="auto_stories" size={14} color="var(--gray-11)" />
        <span style={{ flex: 1, minWidth: 0 }}>{skillReadLabel(command.skills, command.status === 'running')}</span>
      </button>
      {open ? <TerminalBlock command={command} onOpenLog={onOpenLog} /> : null}
    </Box>
  );
}

/** One command as a terminal shows it: `$ command`, its output, how it ended and how long it took. */
function TerminalBlock({ command, onOpenLog }: { command: ActivityCommand; onOpenLog: () => void }) {
  const { t, i18n } = useTranslation();
  const lines = useMemo(() => terminalLines(command.stdout, command.stderr), [command.stdout, command.stderr]);
  const label = command.command || t('activityPanel.command.unnamed');

  // A nested dark theme: badges and buttons keep their contrast on the terminal surface in either app theme.
  return (
    <Theme appearance="dark" hasBackground={false} data-fixed-theme="">
      <Box
        data-testid="activity-command"
        data-status={command.status}
        style={{
          border: `1px solid ${TERMINAL_COLORS.border}`,
          borderRadius: 'var(--radius-3)',
          backgroundColor: TERMINAL_COLORS.surface,
          padding: 'var(--space-2)',
        }}
      >
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <Text
            title={label}
            style={{
              flex: 1,
              minWidth: 0,
              color: TERMINAL_COLORS.text,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              fontSize: 12,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            <span aria-hidden="true" style={{ color: TERMINAL_COLORS.muted }}>
              ${' '}
            </span>
            {label}
          </Text>
          <StatusBadge command={command} />
          {command.durationMs !== null ? (
            <Text size="1" style={{ color: TERMINAL_COLORS.muted, flexShrink: 0 }}>
              {formatDuration(command.durationMs, i18n.language)}
            </Text>
          ) : null}
        </Flex>
        {command.truncated ? (
          <Text as="p" size="1" style={{ color: TERMINAL_COLORS.muted, margin: 'var(--space-1) 0 0' }}>
            {t('activityPanel.command.truncated')}
          </Text>
        ) : null}
        {command.liveDropped > 0 ? (
          <Text as="p" size="1" style={{ color: TERMINAL_COLORS.muted, margin: 'var(--space-1) 0 0' }}>
            {t('activityPanel.command.dropped', { size: formatBytes(command.liveDropped, i18n.language) })}
          </Text>
        ) : null}
        <Box style={{ marginTop: 'var(--space-2)' }}>
          {lines.length > 0 ? (
            <TerminalOutput lines={lines} label={t('activityPanel.command.outputLabel', { command: label })} />
          ) : (
            <Text as="p" size="1" style={{ color: TERMINAL_COLORS.muted, margin: 0 }}>
              {command.status === 'running'
                ? t('activityPanel.command.waitingOutput')
                : command.outputMayBeLost
                  ? t('activityPanel.command.noOutputCaptured')
                  : t('activityPanel.command.noOutput')}
            </Text>
          )}
        </Box>
        {command.hasLog ? (
          <Flex justify="end" style={{ marginTop: 'var(--space-2)' }}>
            <Button
              size="1"
              variant="soft"
              color="gray"
              highContrast
              onClick={onOpenLog}
              aria-label={t('activityPanel.command.fullLogLabel', { command: label })}
            >
              {t('activityPanel.command.fullLog')}
            </Button>
          </Flex>
        ) : null}
      </Box>
    </Theme>
  );
}
