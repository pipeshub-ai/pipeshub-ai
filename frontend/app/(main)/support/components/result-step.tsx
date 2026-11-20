'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Grid, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { SupportRequestResult } from '../types';
import { buildMailto, copyText } from '../utils';

export interface MailDraft {
  to: string;
  subject: string;
  body: string;
}

export type FinishedRequest =
  | { kind: 'saved'; result: SupportRequestResult; mail: MailDraft }
  // Nothing was saved: the hourly limit was reached, or the request could not be sent at all.
  | { kind: 'limit'; mail: MailDraft }
  | { kind: 'unsaved'; mail: MailDraft };

/** Flips to true for a moment after a successful copy. */
function useCopied(): [boolean, (text: string) => Promise<boolean>] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async (text: string) => {
    const ok = await copyText(text);
    if (ok) {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    }
    return ok;
  };
  return [copied, copy];
}

export function needsEmailApp(finished: FinishedRequest): boolean {
  return finished.kind !== 'saved' || finished.result.outcome === 'failed' || finished.result.outcome === 'no_smtp';
}

export function SavedView({ result }: { result: SupportRequestResult }) {
  const { t } = useTranslation();
  const [copied, copy] = useCopied();
  const pending = result.outcome === 'pending';

  return (
    <Flex direction="column" align="center" gap="3" px="5" py="2" style={{ textAlign: 'center' }}>
      <Flex
        align="center"
        justify="center"
        style={{
          width: 56,
          height: 56,
          borderRadius: '50%',
          background: pending ? 'var(--amber-a3)' : 'var(--accent-a3)',
        }}
      >
        <MaterialIcon
          name={pending ? 'schedule' : 'mark_email_read'}
          size={28}
          color={pending ? 'var(--amber-11)' : 'var(--accent-11)'}
        />
      </Flex>
      <Text size="5" weight="bold" style={{ color: 'var(--slate-12)' }}>
        {pending ? t('support.result.pendingTitle') : t('support.result.sentTitle')}
      </Text>
      <Text size="2" style={{ color: 'var(--slate-11)', maxWidth: 440, lineHeight: '20px' }}>
        {pending
          ? t('support.result.pendingBody', { email: result.supportEmail })
          : t('support.result.sentBody')}
      </Text>
      <Flex
        align="center"
        justify="between"
        gap="3"
        mt="2"
        style={{
          width: '100%',
          maxWidth: 440,
          boxSizing: 'border-box',
          padding: '10px 14px',
          borderRadius: 'var(--radius-3)',
          border: '1px dashed var(--accent-7)',
          background: 'var(--accent-a2)',
          textAlign: 'left',
        }}
      >
        <Box>
          <Text as="div" size="1" style={{ color: 'var(--slate-10)' }}>
            {t('support.result.referenceLabel')}
          </Text>
          <Text as="div" size="4" weight="bold" style={{ fontFamily: 'var(--code-font-family)', letterSpacing: 1 }}>
            {result.reference}
          </Text>
        </Box>
        <IconButton
          variant="soft"
          aria-label={t('support.result.copyReference')}
          onClick={() => void copy(result.reference)}
        >
          <MaterialIcon name={copied ? 'check' : 'content_copy'} size={16} />
        </IconButton>
      </Flex>
      <Text size="1" style={{ color: 'var(--slate-10)' }}>
        {t('support.result.quote')}
      </Text>
    </Flex>
  );
}

export function EmailAppView({
  mail,
  fileCount,
  onDone,
}: {
  mail: MailDraft;
  /** Files picked in the form: an email app link cannot carry them. */
  fileCount: number;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [copied, copy] = useCopied();
  const [copyFailed, setCopyFailed] = useState(false);
  const bodyRef = useRef<HTMLPreElement>(null);
  const mailto = buildMailto(mail.to, mail.subject, mail.body);

  const copyMessage = async () => {
    const ok = await copy(`${mail.subject}\n\n${mail.body}`);
    setCopyFailed(!ok);
    if (!ok && bodyRef.current) {
      const range = document.createRange();
      range.selectNodeContents(bodyRef.current);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }
  };

  return (
    <Flex direction="column" gap="3" px="5">
      <Box style={{ borderRadius: 'var(--radius-3)', border: '1px solid var(--olive-a4)', background: 'var(--olive-2)', overflow: 'hidden' }}>
        <Grid columns="72px 1fr" gapX="3" gapY="1" p="3" style={{ borderBottom: '1px solid var(--olive-a4)' }}>
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('support.email.to')}
          </Text>
          <Text size="1" weight="medium" style={{ overflowWrap: 'anywhere' }}>
            {mail.to}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-10)' }}>
            {t('support.email.subject')}
          </Text>
          <Text size="1" weight="medium" style={{ overflowWrap: 'anywhere' }}>
            {mail.subject}
          </Text>
        </Grid>
        <pre
          ref={bodyRef}
          style={{
            margin: 0,
            padding: 12,
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
            fontFamily: 'var(--code-font-family)',
            fontSize: 12,
            lineHeight: '18px',
            color: 'var(--slate-12)',
            maxHeight: 220,
            overflowY: 'auto',
          }}
        >
          {mail.body}
        </pre>
      </Box>
      {mailto.shortened && (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {t('support.email.shortened')}
        </Text>
      )}
      {fileCount > 0 && (
        <Flex gap="2" align="start" style={{ color: 'var(--amber-11)' }}>
          <MaterialIcon name="attach_file" size={14} style={{ marginTop: 2 }} />
          <Text size="1">{t('support.email.attachFiles', { count: fileCount })}</Text>
        </Flex>
      )}
      {copyFailed && (
        <Text size="1" role="status" style={{ color: 'var(--slate-11)' }}>
          {t('support.email.copyFallback')}
        </Text>
      )}
      <Flex
        direction={{ initial: 'column-reverse', sm: 'row' }}
        align={{ initial: 'stretch', sm: 'center' }}
        justify="between"
        gap="3"
        mt="2"
      >
        <Flex justify={{ initial: 'center', sm: 'start' }}>
          <Button type="button" variant="ghost" color="gray" size="2" onClick={onDone}>
            {t('support.result.done')}
          </Button>
        </Flex>
        <Flex direction={{ initial: 'column-reverse', sm: 'row' }} gap="2">
          <Button type="button" variant="outline" color="gray" size="2" onClick={() => void copyMessage()}>
            <MaterialIcon name={copied ? 'check' : 'content_copy'} size={16} />
            {copied ? t('support.email.copied') : t('support.email.copy')}
          </Button>
          <Button asChild size="2">
            <a href={mailto.href}>
              <MaterialIcon name="open_in_new" size={16} />
              {t('support.email.open')}
            </a>
          </Button>
        </Flex>
      </Flex>
    </Flex>
  );
}
