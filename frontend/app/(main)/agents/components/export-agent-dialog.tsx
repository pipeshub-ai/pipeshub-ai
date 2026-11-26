'use client';

import React, { useEffect, useState } from 'react';
import { Callout, Flex, RadioGroup, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ConfirmationDialog } from '@/workspace/components/confirmation-dialog';
import { AgentsApi } from '../api';
import { AgentConfigApi, exportFileName, type ExportVersion } from '../config-transfer';

function saveJson(value: unknown, name: string): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

const valueOf = (version: ExportVersion): string => String(version);
const versionOf = (value: string): ExportVersion => (value === 'draft' ? 'draft' : Number(value));

/**
 * The builder's "Export configuration" (C4.08): the live version by default,
 * another published version, or the unpublished draft, as one `.agent.json`
 * file. What is never carried is said before the download.
 */
export function ExportAgentDialog({
  open,
  onOpenChange,
  agentKey,
  agentName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentKey: string;
  agentName: string;
}) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState<number[] | null>(null);
  const [latest, setLatest] = useState(0);
  const [choice, setChoice] = useState<string>('draft');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setError(null);
    setVersions(null);
    AgentsApi.listAgentVersions(agentKey, { quiet: true })
      .then((list) => {
        if (!live) return;
        const numbers = list.versions.map((v) => v.version).sort((a, b) => b - a);
        setVersions(numbers);
        setLatest(list.latestVersion);
        setChoice(list.latestVersion > 0 ? valueOf(list.latestVersion) : 'draft');
      })
      .catch(() => {
        if (!live) return;
        // Versions could not be read: the server's default is still the live version.
        setVersions([]);
        setLatest(0);
        setChoice('live');
      });
    return () => {
      live = false;
    };
  }, [open, agentKey]);

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      const document = await AgentConfigApi.exportAgent(agentKey, choice === 'live' ? null : versionOf(choice));
      const name = typeof document?.agent === 'object' && document.agent !== null ? String((document.agent as { name?: unknown }).name ?? agentName) : agentName;
      saveJson(document, exportFileName(name));
      onOpenChange(false);
    } catch {
      setError(t('agentTransfer.export.failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('agentTransfer.export.title')}
      confirmLabel={t('agentTransfer.export.download')}
      confirmLoadingLabel={t('agentTransfer.export.downloading')}
      confirmVariant="primary"
      isLoading={busy}
      onConfirm={() => void download()}
      message={
        <Flex direction="column" gap="3">
          <Text size="2">{t('agentTransfer.export.body')}</Text>
          <Text size="2" weight="medium">
            {t('agentTransfer.export.what')}
          </Text>
          {versions === null ? (
            <Text size="2" color="gray">
              {t('agentTransfer.export.loading')}
            </Text>
          ) : (
            <RadioGroup.Root value={choice} onValueChange={setChoice} aria-label={t('agentTransfer.export.what')}>
              {choice === 'live' ? (
                <RadioGroup.Item value="live" style={{ minHeight: 32 }}>
                  {t('agentTransfer.export.liveVersion')}
                </RadioGroup.Item>
              ) : null}
              {versions.map((version) => (
                <RadioGroup.Item key={version} value={valueOf(version)} style={{ minHeight: 32 }}>
                  {version === latest
                    ? t('agentTransfer.export.versionLive', { version })
                    : t('agentTransfer.export.version', { version })}
                </RadioGroup.Item>
              ))}
              <RadioGroup.Item value="draft" style={{ minHeight: 32 }}>
                {t('agentTransfer.export.draft')}
              </RadioGroup.Item>
            </RadioGroup.Root>
          )}
          <Callout.Root color="gray" variant="surface" size="1">
            <Callout.Text>{t('agentTransfer.export.notIncluded')}</Callout.Text>
          </Callout.Root>
          {error ? (
            <Callout.Root color="red" variant="surface" size="1" role="alert">
              <Callout.Text>{error}</Callout.Text>
            </Callout.Root>
          ) : null}
        </Flex>
      }
    />
  );
}
