'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Heading, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { useAuthStore } from '@/config';
import {
  getDesktopApi,
  switchDesktopMode,
  type DesktopMode,
  type DesktopState,
  type LocalServerStatus,
} from '@/lib/electron/desktop-mode';

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <Flex
      align="center"
      justify="center"
      className="viewport-height"
      style={{ width: '100vw', backgroundColor: 'var(--color-background)' }}
    >
      <Box
        style={{
          width: '100%',
          maxWidth: '520px',
          padding: 'var(--space-7)',
          borderRadius: 'var(--radius-4)',
          backgroundColor: 'var(--color-surface)',
          boxShadow: 'var(--shadow-4)',
        }}
      >
        {children}
      </Box>
    </Flex>
  );
}

function Header({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <Flex direction="column" align="center" gap="3" style={{ marginBottom: 'var(--space-6)' }}>
      <img src="/logo/pipes-hub.svg" alt="PipesHub" width={56} height={56} />
      <Heading size="5" align="center">{title}</Heading>
      <Text size="2" color="gray" align="center">{subtitle}</Text>
    </Flex>
  );
}

function ChoiceCard({
  icon, title, description, disabled, testId, onClick,
}: {
  icon: string;
  title: string;
  description: string;
  disabled?: boolean;
  testId: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={disabled}
      onClick={onClick}
      style={{
        display: 'flex',
        gap: 'var(--space-3)',
        alignItems: 'flex-start',
        textAlign: 'left',
        width: '100%',
        padding: 'var(--space-4)',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--gray-6)',
        backgroundColor: 'var(--color-surface)',
        color: 'var(--gray-12)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.55 : 1,
        minHeight: 44,
      }}
    >
      <MaterialIcon name={icon} size={28} color="var(--accent-11)" />
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium">{title}</Text>
        <Text size="2" color="gray">{description}</Text>
      </Flex>
    </button>
  );
}

/** First run: run PipesHub here, or connect to a server (today's behaviour). */
export function ModeChoiceScreen({
  localAvailable,
  onChoose,
}: {
  localAvailable: boolean;
  onChoose: (mode: DesktopMode) => void;
}) {
  const { t } = useTranslation();
  return (
    <Shell>
      <Header title={t('electron.desktopMode.choice.title')} subtitle={t('electron.desktopMode.choice.subtitle')} />
      <Flex direction="column" gap="3">
        <ChoiceCard
          testId="mode-local"
          icon="computer"
          title={t('electron.desktopMode.choice.local.title')}
          description={
            localAvailable
              ? t('electron.desktopMode.choice.local.description')
              : t('electron.desktopMode.choice.local.unavailable')
          }
          disabled={!localAvailable}
          onClick={() => onChoose('local')}
        />
        <ChoiceCard
          testId="mode-remote"
          icon="dns"
          title={t('electron.desktopMode.choice.remote.title')}
          description={t('electron.desktopMode.choice.remote.description')}
          onClick={() => onChoose('remote')}
        />
      </Flex>
    </Shell>
  );
}

const PHASE_KEYS = new Set(['checking']);

/** Progress while the local server starts; the error with retry when it does not. */
export function LocalStartScreen({ status, state }: { status: LocalServerStatus; state: DesktopState | null }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const desktop = getDesktopApi();

  if (status.state === 'failed') {
    const code = status.error?.code ?? 'crashed';
    return (
      <Shell>
        <Header title={t('electron.desktopMode.local.failedTitle')} subtitle={t(`electron.desktopMode.local.errors.${code}`)} />
        {status.error?.logTail?.length ? (
          <Box
            data-testid="local-error-details"
            style={{
              maxHeight: 140,
              overflow: 'auto',
              padding: 'var(--space-3)',
              marginBottom: 'var(--space-4)',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--gray-3)',
              fontFamily: 'var(--code-font-family)',
              fontSize: 'var(--font-size-1)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {status.error.logTail.slice(-8).join('\n')}
          </Box>
        ) : null}
        <Flex direction="column" gap="2">
          <Button
            size="3"
            disabled={busy}
            data-testid="local-retry"
            style={{ cursor: 'pointer' }}
            onClick={async () => {
              setBusy(true);
              try { await desktop?.retryLocal(); } finally { setBusy(false); }
            }}
          >
            {t('electron.desktopMode.local.retry')}
          </Button>
          <Flex gap="2">
            <Button size="2" variant="soft" style={{ flex: 1, cursor: 'pointer' }} onClick={() => void desktop?.openLogs()}>
              {t('electron.desktopMode.local.openLogs')}
            </Button>
            <Button size="2" variant="soft" color="gray" style={{ flex: 1, cursor: 'pointer' }} onClick={() => void switchDesktopMode('remote', () => useAuthStore.getState().logout())}>
              {t('electron.desktopMode.local.useServer')}
            </Button>
          </Flex>
        </Flex>
      </Shell>
    );
  }

  const phase = status.phase && PHASE_KEYS.has(status.phase) ? t(`electron.desktopMode.local.phases.${status.phase}`) : status.phase;
  return (
    <Shell>
      <Header title={t('electron.desktopMode.local.startingTitle')} subtitle={t('electron.desktopMode.local.startingSubtitle')} />
      <Flex direction="column" align="center" gap="3" data-testid="local-starting">
        <Spinner size={28} />
        <Text size="2" color="gray">{phase || t('electron.desktopMode.local.phases.starting')}</Text>
        {state?.dataDir ? (
          <Text size="1" color="gray" align="center" style={{ wordBreak: 'break-all' }}>
            {t('electron.desktopMode.local.dataFolder', { path: state.dataDir })}
          </Text>
        ) : null}
      </Flex>
    </Shell>
  );
}
