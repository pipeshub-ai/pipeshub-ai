'use client';

import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Code, Dialog, Flex, Text, TextField } from '@radix-ui/themes';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

export const TRIGGER_TOKEN_HEADER = 'X-PipesHub-Trigger-Token';

/** What a webhook schedule's creation answered once: a signing key (the default, TR-04) or a token. */
export interface WebhookCredential {
  scheduleId: string;
  kind: 'signature' | 'token';
  value: string;
}

/** A shell example of one signed call: the timestamp and an HMAC-SHA256 of "<timestamp>.<body>". */
export function signingExample(url: string): string {
  return [
    `body='{"hello":"world"}'`,
    'ts=$(date +%s)',
    `sig=$(printf '%s.%s' "$ts" "$body" | openssl dgst -sha256 -hmac "$SECRET" -hex | cut -d' ' -f2)`,
    `curl -X POST '${url}' \\`,
    `  -H "X-PipesHub-Timestamp: $ts" -H "X-PipesHub-Signature: v1=$sig" \\`,
    `  -H 'Content-Type: application/json' --data "$body"`,
  ].join('\n');
}

export function webhookUrl(scheduleId: string): string {
  const base = getApiBaseUrl() || (typeof window !== 'undefined' ? window.location.origin : '');
  return `${base.replace(/\/$/, '')}/api/v1/agent-harness/triggers/${encodeURIComponent(scheduleId)}/webhook`;
}

/**
 * A webhook schedule's secret, shown once: the page drops it when this closes
 * (the parent clears it). A signed webhook's key comes with how to sign a call;
 * a token says which header carries it.
 */
export function WebhookSecretDialog({
  secret,
  onClose,
}: {
  secret: WebhookCredential | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [copied, setCopied] = useState<'url' | 'token' | 'example' | null>(null);

  const copy = async (what: 'url' | 'token' | 'example', value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(what);
    } catch {
      setCopied(null);
    }
  };

  const url = secret ? webhookUrl(secret.scheduleId) : '';
  const signed = secret?.kind === 'signature';
  const example = signed ? signingExample(url) : '';

  return (
    <Dialog.Root
      open={secret !== null}
      onOpenChange={(open) => {
        if (!open) {
          setCopied(null);
          onClose();
        }
      }}
    >
      <Dialog.Content style={{ maxWidth: '40rem', minWidth: 0 }} aria-describedby={`${id}-once`} data-webhook-auth={secret?.kind}>
        <Dialog.Title>{t('schedules.webhook.title')}</Dialog.Title>
        {secret && (
          <Flex direction="column" gap="3" style={{ minWidth: 0 }}>
            <Text id={`${id}-once`} size="2" role="note" style={{ color: 'var(--amber-11)' }}>
              {t(signed ? 'schedules.webhook.signedShownOnce' : 'schedules.webhook.shownOnce')}
            </Text>
            <Flex direction="column" gap="1">
              <Text as="label" htmlFor={`${id}-url`} size="2" weight="medium">
                {t('schedules.webhook.url')}
              </Text>
              <Flex gap="2">
                <TextField.Root id={`${id}-url`} readOnly value={url} style={{ flex: 1, minWidth: 0 }} />
                <Button type="button" variant="soft" onClick={() => void copy('url', url)}>
                  {copied === 'url' ? t('schedules.webhook.copied') : t('schedules.webhook.copyUrl')}
                </Button>
              </Flex>
            </Flex>
            <Flex direction="column" gap="1">
              <Text as="label" htmlFor={`${id}-token`} size="2" weight="medium">
                {t('schedules.webhook.secret')}
              </Text>
              <Flex gap="2">
                <TextField.Root
                  id={`${id}-token`}
                  readOnly
                  value={secret.value}
                  autoComplete="off"
                  style={{ flex: 1, fontFamily: 'monospace' }}
                />
                <Button type="button" variant="soft" onClick={() => void copy('token', secret.value)}>
                  {copied === 'token' ? t('schedules.webhook.copied') : t('schedules.webhook.copySecret')}
                </Button>
              </Flex>
            </Flex>
            {signed ? (
              <Flex direction="column" gap="2" style={{ minWidth: 0 }}>
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('schedules.webhook.signHowTo')}
                </Text>
                <Box
                  role="region"
                  aria-label={t('schedules.webhook.example')}
                  style={{
                    background: 'var(--olive-2)',
                    border: '1px solid var(--olive-5)',
                    borderRadius: 'var(--radius-2)',
                    padding: 'var(--space-2) var(--space-3)',
                    overflowX: 'auto',
                    // Zero width with a full minimum: the example scrolls instead of widening the dialog past a phone.
                    width: 0,
                    minWidth: '100%',
                  }}
                >
                  <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.5, whiteSpace: 'pre', fontFamily: 'var(--code-font-family, monospace)' }}>
                    {example}
                  </pre>
                </Box>
                <Flex>
                  <Button type="button" variant="soft" onClick={() => void copy('example', example)}>
                    {copied === 'example' ? t('schedules.webhook.copied') : t('schedules.webhook.copyExample')}
                  </Button>
                </Flex>
              </Flex>
            ) : (
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {t('schedules.webhook.howTo')} <Code>{TRIGGER_TOKEN_HEADER}</Code>
              </Text>
            )}
            <Flex justify="end">
              <Dialog.Close>
                <Button type="button">{t('schedules.webhook.done')}</Button>
              </Dialog.Close>
            </Flex>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
