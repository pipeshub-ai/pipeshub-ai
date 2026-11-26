'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Button, Dialog, Flex, Spinner, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatApi } from '../../api';
import { redactText } from '../../approval-preview';
import { plainTerminalText } from '../../code-tasks';
import type { ActivityCommand, DurableAgentRunHandle } from '../../types';
import { TerminalOutput } from './terminal-output';

type LogState = { status: 'loading' } | { status: 'ready'; text: string } | { status: 'failed' };

function download(text: string, commandId: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `command-${commandId}.log`;
  link.click();
  URL.revokeObjectURL(url);
}

/** A command's full redacted log, read on demand (it can be up to 1 MiB), windowed like the live output. */
export function FullLogDialog({
  run,
  command,
  onClose,
}: {
  run: DurableAgentRunHandle;
  command: ActivityCommand;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<LogState>({ status: 'loading' });
  const { commandId } = command;

  useEffect(() => {
    let alive = true;
    ChatApi.getCommandLog(run, commandId)
      .then((text) => alive && setState({ status: 'ready', text: redactText(text).text }))
      .catch(() => alive && setState({ status: 'failed' }));
    return () => {
      alive = false;
    };
  }, [run, commandId]);

  const lines = useMemo(() => {
    if (state.status !== 'ready') return [];
    const text = plainTerminalText(state.text);
    const all = text.endsWith('\n') ? text.slice(0, -1).split('\n') : text.split('\n');
    return all.map((line) => ({ text: line, stream: 'stdout' as const }));
  }, [state]);

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Content maxWidth="960px" aria-describedby={undefined}>
        <Dialog.Title size="3">{t('activityPanel.log.title')}</Dialog.Title>
        <Text as="p" size="1" color="gray" style={{ fontFamily: 'monospace', marginBottom: 'var(--space-3)', wordBreak: 'break-all' }}>
          $ {command.command}
        </Text>
        {state.status === 'loading' ? (
          <Flex align="center" gap="2">
            <Spinner size="1" />
            <Text size="2">{t('activityPanel.log.loading')}</Text>
          </Flex>
        ) : null}
        {state.status === 'failed' ? (
          <Text size="2" color="red" role="alert">
            {t('activityPanel.log.failed')}
          </Text>
        ) : null}
        {state.status === 'ready' ? (
          <TerminalOutput lines={lines} label={t('activityPanel.log.label', { command: command.command })} maxHeight={480} />
        ) : null}
        <Flex justify="end" gap="2" style={{ marginTop: 'var(--space-3)' }}>
          {state.status === 'ready' ? (
            <Button variant="soft" onClick={() => download(state.text, commandId)}>
              {t('activityPanel.log.download')}
            </Button>
          ) : null}
          <Dialog.Close>
            <Button variant="soft" color="gray">
              {t('common.close')}
            </Button>
          </Dialog.Close>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
