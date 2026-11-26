'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Link, Text } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import { ConfirmationDialog } from '../../components/confirmation-dialog';
import { SettingsSection } from '../../components/settings-section';
import { isAccountsError, toAccountsError } from '../api';
import {
  EngineSignInApi,
  SIGNIN_ADDON_GUIDE,
  SIGNIN_KEEP_DEFAULT,
  type EngineSignInView,
  type SignInKeep,
} from '../engine-signin-api';
import { formatAccountDate, LoadState, type Load } from './load-state';

const K = 'engineSignIn';
/** Where a person revokes PipesHub-hosted sessions and sees their usage: Anthropic's own pages. */
export const CLAUDE_SETTINGS_URL = 'https://claude.ai/settings';

/** A plan kind as Claude Code reports it, named for people. */
export function planKey(plan: string | null): string {
  switch (plan) {
    case 'pro':
      return `${K}.plan.pro`;
    case 'max':
      return `${K}.plan.max`;
    case 'team':
      return `${K}.plan.team`;
    case 'enterprise':
      return `${K}.plan.enterprise`;
    default:
      return `${K}.plan.other`;
  }
}

function Chip({ tone, children }: { tone: 'ok' | 'wait' | 'bad' | 'none'; children: React.ReactNode }) {
  const color = tone === 'ok' ? 'green' : tone === 'wait' ? 'amber' : tone === 'bad' ? 'red' : 'gray';
  return (
    <Badge color={color} variant="soft" radius="full">
      {children}
    </Badge>
  );
}

function Lines({ heading, items }: { heading: string; items: string[] }) {
  return (
    <Box>
      <Text as="div" size="2" weight="medium">
        {heading}
      </Text>
      <Box asChild style={{ margin: 'var(--space-1) 0 0', paddingLeft: 'var(--space-4)' }}>
        <ul>
          {items.map((item) => (
            <li key={item}>
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {item}
              </Text>
            </li>
          ))}
        </ul>
      </Box>
    </Box>
  );
}

/**
 * What PipesHub can and cannot do with a person's Claude account (the consent text, SIGNIN §2.6), with how long a
 * sign-in lasts on this deployment (SIGNIN-5): never a number the server did not report.
 */
export function ClaudeConsent({ keep = SIGNIN_KEEP_DEFAULT }: { keep?: SignInKeep }) {
  const { t } = useTranslation();
  const lasts = `${t(`${K}.cannot.keep`, { count: keep.idleMinutes })} ${t(`${K}.cannot.keepMax`, { count: keep.maxHours })}`;
  return (
    <Flex
      direction="column"
      gap="2"
      style={{ background: 'var(--olive-3)', borderRadius: 'var(--radius-2)', padding: 'var(--space-3)' }}
    >
      <Lines heading={t(`${K}.can.heading`)} items={[t(`${K}.can.run`), t(`${K}.can.plan`)]} />
      <Lines
        heading={t(`${K}.cannot.heading`)}
        items={[t(`${K}.cannot.see`), t(`${K}.cannot.others`), lasts]}
      />
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t(`${K}.consent.limits`)}
      </Text>
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t(`${K}.consent.operator`)}
      </Text>
    </Flex>
  );
}

/**
 * SIGNGAP: this server cannot carry a person's own sign-in (the sign-in add-on is off), said plainly, with the
 * section of the install guide that turns it on. `member` adds what happens to the person's tasks meanwhile.
 */
export function SignInAddonReason({ member = false }: { member?: boolean }) {
  const { t } = useTranslation();
  return (
    <Text as="p" size="2" data-testid="signin-addon-reason" style={{ color: 'var(--slate-11)', margin: 0 }}>
      {t(`${K}.unavailable.reason`)}
      {member ? ` ${t(`${K}.unavailable.member`)}` : ''}{' '}
      <Link href={SIGNIN_ADDON_GUIDE} target="_blank" rel="noopener noreferrer">
        {t(`${K}.unavailable.guide`)}
      </Link>
    </Text>
  );
}

function ClaudeRow({ view, onChange }: { view: EngineSignInView; onChange: (view: EngineSignInView) => void }) {
  const { t, i18n } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const claude = view.claude;

  const run = async (action: () => Promise<EngineSignInView>, done: string) => {
    setBusy(true);
    setError(null);
    try {
      onChange(await action());
      addToast({ variant: 'success', title: t(done) });
    } catch (caught) {
      const failure = isAccountsError(caught) ? caught : toAccountsError(caught, true);
      setError(
        failure.status === 409
          ? t(`${K}.unavailable.reason`)
          : failure.kind === 'forbidden'
            ? t(`${K}.error.notAllowed`)
            : t('aiAccounts.common.failed'),
      );
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const date = (iso: string | null) => (iso === null ? '' : formatAccountDate(iso, i18n.language));
  let chip: React.ReactNode;
  let body: React.ReactNode;
  let actions: React.ReactNode = null;
  switch (claude.state) {
    case 'not_allowed':
      if (claude.reason === 'engine_signin_unavailable') {
        chip = <Chip tone="none">{t(`${K}.state.unavailable`)}</Chip>;
        body = <SignInAddonReason member />;
        // Greyed, not hidden: the person sees what they could turn on once the server can carry it.
        actions = (
          <Button type="button" style={{ minHeight: 44 }} disabled>
            {t(`${K}.action.useMine`)}
          </Button>
        );
        break;
      }
      chip = <Chip tone="none">{t(`${K}.state.notAllowed`)}</Chip>;
      body = (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {claude.reason === 'account_requires_interactive' ? t(`${K}.notAllowed.service`) : t(`${K}.notAllowed.org`)}
        </Text>
      );
      break;
    case 'connected':
      chip = <Chip tone="ok">{t(`${K}.state.connected`)}</Chip>;
      body = (
        <Flex direction="column" gap="1">
          <Text size="2" data-testid="claude-signed-in-as">
            {t(`${K}.connected.as`, { email: claude.email ?? '', plan: t(planKey(claude.plan)) })}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t(`${K}.connected.when`, { date: date(claude.signedInAt) })}
          </Text>
        </Flex>
      );
      break;
    case 'disconnected':
      chip = <Chip tone="none">{t(`${K}.state.disconnected`)}</Chip>;
      body = (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t(`${K}.disconnected.body`, { date: date(claude.disconnectedAt) })}{' '}
          <a href={CLAUDE_SETTINGS_URL} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-11)' }}>
            {t(`${K}.revokeAtClaude`)}
          </a>
        </Text>
      );
      break;
    default:
      chip = claude.enabled ? (
        <Chip tone="wait">{t(`${K}.state.onWaiting`)}</Chip>
      ) : (
        <Chip tone="none">{t(`${K}.state.available`)}</Chip>
      );
      body = claude.enabled ? (
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t(`${K}.available.onBody`)}
        </Text>
      ) : (
        <ClaudeConsent keep={claude.keep} />
      );
  }
  if (claude.state !== 'not_allowed') {
    actions = claude.enabled ? (
      <Button
        type="button"
        color="red"
        variant="soft"
        style={{ minHeight: 44 }}
        disabled={busy}
        onClick={() => setConfirming(true)}
      >
        {t(`${K}.action.disconnect`)}
      </Button>
    ) : (
      <Button
        type="button"
        style={{ minHeight: 44 }}
        disabled={busy}
        onClick={() => void run(() => EngineSignInApi.setClaude(true), `${K}.toast.on`)}
      >
        {claude.state === 'disconnected' ? t(`${K}.action.connectAgain`) : t(`${K}.action.useMine`)}
      </Button>
    );
  }

  return (
    <Flex direction="column" gap="2" role="group" aria-label={t(`${K}.claude.title`)} data-signin-state={claude.state}>
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="2" weight="medium" style={{ flex: '1 1 auto' }}>
          {t(`${K}.claude.title`)}
        </Text>
        {chip}
      </Flex>
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t(`${K}.claude.engine`)}
      </Text>
      {body}
      {claude.state !== 'not_allowed' && (
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {claude.consumerPlans ? t(`${K}.plans.any`) : t(`${K}.plans.workOnly`)}
        </Text>
      )}
      {error && (
        <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
          {error}
        </Text>
      )}
      {actions && <Flex gap="2" wrap="wrap">{actions}</Flex>}
      <ConfirmationDialog
        open={confirming}
        onOpenChange={(open) => !open && setConfirming(false)}
        title={t(`${K}.confirm.title`)}
        message={<Text size="2">{t(`${K}.confirm.message`)}</Text>}
        confirmLabel={t(`${K}.action.disconnect`)}
        confirmLoadingLabel={t(`${K}.action.disconnecting`)}
        isLoading={busy}
        onConfirm={() => void run(() => EngineSignInApi.disconnectClaude(), `${K}.toast.off`)}
      />
    </Flex>
  );
}

function ChatGptRow() {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="2" role="group" aria-label={t(`${K}.chatgpt.title`)} data-signin-state="waiting_vendor">
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="2" weight="medium" style={{ flex: '1 1 auto' }}>
          {t(`${K}.chatgpt.title`)}
        </Text>
        <Chip tone="none">{t(`${K}.state.waitingVendor`)}</Chip>
      </Flex>
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t(`${K}.chatgpt.body`)}
      </Text>
      <Flex>
        <Button type="button" disabled style={{ minHeight: 44 }}>
          {t(`${K}.action.connect`)}
        </Button>
      </Flex>
    </Flex>
  );
}

/** "Coding engine sign-in" on My AI accounts (SIGNIN §4): a person's own Claude account in Claude Code. */
export function EngineSignInSection() {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load<EngineSignInView>>({ status: 'loading' });

  const read = useCallback(async () => {
    setLoad({ status: 'loading' });
    try {
      setLoad({ status: 'ready', data: await EngineSignInApi.me() });
    } catch (caught) {
      setLoad({ status: 'error', error: isAccountsError(caught) ? caught : toAccountsError(caught) });
    }
  }, []);

  useEffect(() => {
    void read();
  }, [read]);

  return (
    <SettingsSection title={t(`${K}.title`)} description={t(`${K}.description`)}>
      <LoadState load={load} onRetry={() => void read()}>
        {(view) => (
          <Flex direction="column" gap="4">
            <ClaudeRow view={view} onChange={(next) => setLoad({ status: 'ready', data: next })} />
            <Box style={{ borderTop: '1px solid var(--olive-4)' }} />
            <ChatGptRow />
          </Flex>
        )}
      </LoadState>
    </SettingsSection>
  );
}
