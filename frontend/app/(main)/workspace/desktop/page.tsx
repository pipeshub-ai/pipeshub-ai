'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, Heading, Button, Switch } from '@radix-ui/themes';
import { SettingsSection, SettingsRow } from '../components';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';
import { useToastStore } from '@/lib/store/toast-store';
import { useAuthStore } from '@/lib/store/auth-store';
import {
  getDesktopApi,
  getLocalRepoApi,
  selectFolder,
  switchDesktopMode,
  useDesktopState,
  type LocalRepoSummary,
} from '@/lib/electron/desktop-mode';

const POLL_MS = 5000;

export default function DesktopSettingsPage() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const { state } = useDesktopState();
  const [repos, setRepos] = useState<LocalRepoSummary[]>([]);
  const [adding, setAdding] = useState(false);
  const repoApi = getLocalRepoApi();
  const running = state?.local.state === 'running';

  const reload = useCallback(async () => {
    setRepos((await getLocalRepoApi()?.list()) ?? []);
  }, []);

  // Indexing progress comes from the server; poll while the page is open.
  useEffect(() => {
    if (!running) return;
    void reload();
    const id = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(id);
  }, [running, reload]);

  const addRepository = async () => {
    const rootPath = await selectFolder();
    if (!rootPath || !repoApi) return;
    setAdding(true);
    try {
      const result = await repoApi.add(rootPath);
      if (!result.ok) {
        addToast({ variant: 'error', title: t('workspace.desktop.repos.addError'), description: result.error });
      }
      await reload();
    } finally {
      setAdding(false);
    }
  };

  const removeRepository = async (repoKey: string) => {
    const result = await repoApi?.remove(repoKey);
    if (result && !result.ok) {
      addToast({ variant: 'error', title: t('workspace.desktop.repos.removeError'), description: result.error });
    }
    await reload();
  };

  if (!state || state.mode !== 'local') {
    return (
      <Box style={{ height: '100%', overflowY: 'auto' }}>
        <SettingsPageContainer>
          <Text size="2" color="gray">{t('workspace.desktop.notLocal')}</Text>
        </SettingsPageContainer>
      </Box>
    );
  }

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer>
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.desktop.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-10)', marginTop: 'var(--space-1)', display: 'block' }}>
            {t('workspace.desktop.subtitle')}
          </Text>
        </Box>

        <Flex direction="column" gap="5">
          <SettingsSection title={t('workspace.desktop.server.title')}>
            <SettingsRow
              label={t('workspace.desktop.server.status')}
              description={running ? state.local.url : t(`workspace.desktop.server.states.${state.local.state}`)}
            >
              <Text size="2" data-testid="local-server-state">
                {t(`workspace.desktop.server.states.${state.local.state}`)}
              </Text>
            </SettingsRow>
            <SettingsRow
              label={t('workspace.desktop.server.keepRunning')}
              description={t('workspace.desktop.server.keepRunningDescription')}
            >
              <Switch
                checked={state.keepRunningInTray}
                onCheckedChange={(value) => void getDesktopApi()?.setKeepRunning(value)}
                aria-label={t('workspace.desktop.server.keepRunning')}
              />
            </SettingsRow>
            <SettingsRow label={t('workspace.desktop.server.dataFolder')} description={state.dataDir}>
              <Button size="2" variant="soft" style={{ cursor: 'pointer' }} onClick={() => void getDesktopApi()?.openLogs()}>
                {t('workspace.desktop.server.openLogs')}
              </Button>
            </SettingsRow>
            <SettingsRow
              label={t('workspace.desktop.server.useServer')}
              description={t('workspace.desktop.server.useServerDescription')}
            >
              <Button
                size="2"
                variant="soft"
                color="gray"
                style={{ cursor: 'pointer' }}
                onClick={() => void switchDesktopMode('remote', () => useAuthStore.getState().logout())}
              >
                {t('workspace.desktop.server.useServerAction')}
              </Button>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            title={t('workspace.desktop.repos.title')}
            description={t('workspace.desktop.repos.description')}
            rightAction={
              <Button size="2" disabled={!running || adding} style={{ cursor: 'pointer' }} onClick={() => void addRepository()}>
                {adding ? t('workspace.desktop.repos.adding') : t('workspace.desktop.repos.add')}
              </Button>
            }
          >
            {repos.length === 0 ? (
              <Text size="2" color="gray">{t('workspace.desktop.repos.empty')}</Text>
            ) : (
              repos.map((repo) => (
                <SettingsRow
                  key={repo.repoKey}
                  label={repo.name}
                  description={`${repo.rootPath} · ${t('workspace.desktop.repos.files', { count: repo.files })}`}
                >
                  <Flex align="center" gap="3">
                    <Text size="2" color="gray">{t(`workspace.desktop.repos.states.${repo.state}`, { defaultValue: repo.state })}</Text>
                    <Button size="1" variant="ghost" color="red" style={{ cursor: 'pointer' }} onClick={() => void removeRepository(repo.repoKey)}>
                      {t('workspace.desktop.repos.remove')}
                    </Button>
                  </Flex>
                </SettingsRow>
              ))
            )}
          </SettingsSection>
        </Flex>
      </SettingsPageContainer>
    </Box>
  );
}
