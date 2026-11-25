'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SecretTextField } from '@/app/components/ui/secret-text-field';
import { isAccountsError } from '@/app/(main)/workspace/ai-accounts/api';
import { anthropicLink, EngineSignInApi, type SignInCodeResult } from '@/app/(main)/workspace/ai-accounts/engine-signin-api';
import { planKey } from '@/app/(main)/workspace/ai-accounts/components/engine-signin-section';
import { signInReasonKey, type EngineSignInLine } from '../../engine-signin';
import { cardSendRunning, sendCard, sendFailureOf } from '../../card-send';
import { CardSendNotice } from './card-send-notice';

const K = 'engineSignIn.card';

type Shown =
  | { kind: 'loading' }
  | { kind: 'waiting'; link: string | null }
  | { kind: 'ended' }
  | { kind: 'signed_in'; plan: string | null }
  | { kind: 'failed'; reasonCode: string | null }
  | { kind: 'org_account' };

/** "Using your Claude plan" beside a task signed in on the person's own plan: no dollar cost is shown for it. */
export function OwnPlanChip({ plan }: { plan: string | null }) {
  const { t } = useTranslation();
  return (
    <Badge color="jade" variant="soft" radius="full" data-testid="own-plan-chip" style={{ alignSelf: 'flex-start' }}>
      <MaterialIcon name="verified_user" size={14} />
      {plan ? t(`${K}.usingPlanNamed`, { plan: t(planKey(plan)) }) : t(`${K}.usingPlan`)}
    </Badge>
  );
}

/**
 * Claude Code in the person's sandbox asks them to sign in (SIGNIN §4): the
 * link to Anthropic's own page (its host checked twice) and a field for the
 * code Anthropic shows, which goes straight to Claude Code and is never kept.
 * Never a silent fallback: using the organization's account is the person's
 * explicit choice, and it applies to the message they send next.
 */
export function EngineSignInCard({ line, active }: { line: EngineSignInLine; active: boolean }) {
  const { t } = useTranslation();
  const [shown, setShown] = useState<Shown>(() =>
    line.state === 'signed_in'
      ? { kind: 'signed_in', plan: line.plan }
      : line.state === 'failed'
        ? { kind: 'failed', reasonCode: line.reasonCode }
        : { kind: 'loading' },
  );
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const describe = useCallback(async () => {
    try {
      const attempt = await EngineSignInApi.attempt(line.attemptId);
      setShown(attempt.state === 'waiting' ? { kind: 'waiting', link: anthropicLink(attempt.link) } : { kind: 'ended' });
    } catch (caught) {
      setShown(isAccountsError(caught) && caught.kind === 'not_found' ? { kind: 'ended' } : { kind: 'failed', reasonCode: null });
    }
  }, [line.attemptId]);

  useEffect(() => {
    if (line.state === 'signed_in') setShown({ kind: 'signed_in', plan: line.plan });
    else if (line.state === 'failed') setShown({ kind: 'failed', reasonCode: line.reasonCode });
    else if (active) void describe();
    else setShown({ kind: 'ended' });
  }, [line.state, line.plan, line.reasonCode, active, describe]);

  // CARDFAIL: the code goes through the cards' shared send. A code is single-use at Anthropic and the server cannot
  // tell a second copy from a new one, so nothing is resent unasked: a transient failure says why and offers Try again.
  const sendKey = `engine-signin:${line.attemptId}`;
  const submit = async (value: string = code.trim()) => {
    if (!value || cardSendRunning(sendKey)) return;
    setBusy(true);
    setNotice(null);
    let caught: unknown = null;
    const outcome = await sendCard<SignInCodeResult>(
      sendKey,
      async () => {
        try {
          return { sent: await EngineSignInApi.submitCode(line.attemptId, value) };
        } catch (error) {
          caught = error;
          return { failed: isAccountsError(error) ? sendFailureOf(error.status, null) : { retry: false, code: null } };
        }
      },
      { again: () => void submit(value), autoRetry: false },
    );
    setBusy(false);
    if ('sent' in outcome) {
      const result = outcome.sent;
      setCode('');
      if (result.state === 'signed_in') {
        setShown({ kind: 'signed_in', plan: result.plan });
      } else if (result.retry) {
        setNotice(t(signInReasonKey(result.reasonCode) ?? 'engineSignIn.reason.failed'));
        await describe();
      } else {
        setShown({ kind: 'failed', reasonCode: result.reasonCode });
      }
    } else if ('refused' in outcome) {
      setNotice(
        isAccountsError(caught) && caught.kind === 'invalid'
          ? t('engineSignIn.reason.codeInvalid')
          : isAccountsError(caught) && caught.kind === 'not_found'
            ? t(`${K}.ended`)
            : t('aiAccounts.common.failed'),
      );
    }
  };

  const cancel = async (useOrg: boolean) => {
    setBusy(true);
    try {
      if (useOrg) await EngineSignInApi.setClaude(false);
      await EngineSignInApi.cancel(line.attemptId).catch(() => undefined);
      setShown(useOrg ? { kind: 'org_account' } : { kind: 'failed', reasonCode: 'signin_cancelled' });
    } catch {
      setNotice(t('aiAccounts.common.failed'));
    } finally {
      setBusy(false);
    }
  };

  if (shown.kind === 'signed_in') return <OwnPlanChip plan={shown.plan} />;

  return (
    <Box
      role="group"
      aria-labelledby={`signin-${line.attemptId}`}
      data-testid="engine-signin-card"
      data-signin-state={shown.kind}
      style={{
        border: '1px solid var(--amber-a6)',
        background: 'var(--amber-a2)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
      }}
    >
      <Flex direction="column" gap="2">
        <Flex align="center" gap="2">
          <MaterialIcon name="key" size={16} color="var(--amber-11)" />
          <Text id={`signin-${line.attemptId}`} size="2" weight="medium" data-testid="interaction-heading">
            {t(`${K}.title`)}
          </Text>
        </Flex>
        {shown.kind === 'loading' && (
          <Text size="2" role="status" style={{ color: 'var(--slate-11)' }}>
            {t('aiAccounts.common.loading')}
          </Text>
        )}
        {shown.kind === 'waiting' && (
          <>
            <Text size="2">{t(`${K}.body`)}</Text>
            {shown.link ? (
              <Button asChild variant="soft" style={{ alignSelf: 'flex-start', minHeight: 44 }}>
                <a href={shown.link} target="_blank" rel="noopener noreferrer">
                  {t(`${K}.open`)}
                  <MaterialIcon name="open_in_new" size={14} />
                </a>
              </Button>
            ) : (
              <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
                {t('engineSignIn.reason.urlRefused')}
              </Text>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (code.trim()) void submit();
              }}
            >
              <Flex gap="2" wrap="wrap" align="end">
                <Flex direction="column" gap="1" style={{ flex: '1 1 220px' }}>
                  <Text as="label" htmlFor={`signin-code-${line.attemptId}`} size="1" weight="medium">
                    {t(`${K}.codeLabel`)}
                  </Text>
                  <SecretTextField
                    id={`signin-code-${line.attemptId}`}
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    disabled={busy}
                    style={{ minHeight: 44 }}
                  />
                </Flex>
                <Button type="submit" disabled={busy || !code.trim()} style={{ minHeight: 44 }}>
                  {busy ? t(`${K}.checking`) : t(`${K}.continue`)}
                </Button>
              </Flex>
            </form>
            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t(`${K}.privacy`)}
            </Text>
          </>
        )}
        {shown.kind === 'ended' && (
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t(`${K}.ended`)}
          </Text>
        )}
        {shown.kind === 'failed' && (
          <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
            {t(signInReasonKey(shown.reasonCode) ?? 'engineSignIn.reason.failed')}
          </Text>
        )}
        {shown.kind === 'org_account' && (
          <Text size="2" role="status">
            {t(`${K}.orgChosen`)}
          </Text>
        )}
        <CardSendNotice sendKey={sendKey} what="code" />
        {notice && (
          <Text size="2" role="alert" style={{ color: 'var(--red-11)' }}>
            {notice}
          </Text>
        )}
        {((shown.kind === 'waiting' && active) || shown.kind === 'failed') && (
          <Flex gap="2" wrap="wrap">
            <Button
              type="button"
              variant="soft"
              color="gray"
              disabled={busy}
              onClick={() => void cancel(true)}
              style={{ minHeight: 44 }}
            >
              {t(`${K}.useOrg`)}
            </Button>
            {shown.kind === 'waiting' && active && (
              <Button
                type="button"
                variant="ghost"
                color="gray"
                disabled={busy}
                onClick={() => void cancel(false)}
                style={{ minHeight: 44 }}
              >
                {t('common.cancel')}
              </Button>
            )}
          </Flex>
        )}
      </Flex>
    </Box>
  );
}
