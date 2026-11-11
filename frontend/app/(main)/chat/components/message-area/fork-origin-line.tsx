'use client';

import React from 'react';
import Link from 'next/link';
import { Box, Flex, Link as ThemeLink, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { buildChatHref } from '../../build-chat-url';
import type { ForkChild, ForkOrigin } from '../../types';

const RULE_STYLE: React.CSSProperties = { flex: 1, height: 1, background: 'var(--slate-a5)' };

/** Top of a fork: where it was copied from, as a link, so it is clear before any scrolling. */
export function ForkOriginHeader({
  origin,
  agentId,
  projectId,
}: {
  origin: ForkOrigin;
  agentId: string | null;
  projectId: string | null;
}) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="1"
      wrap="wrap"
      data-testid="fork-origin-header"
      style={{ color: 'var(--slate-11)', minWidth: 0 }}
    >
      <MaterialIcon name="subdirectory_arrow_right" size={14} color="var(--slate-11)" />
      <Text size="2">{t('chat.forkedFrom')}</Text>
      {origin.title === null ? (
        <Text size="2">· {t('chat.forkOriginUnavailable')}</Text>
      ) : (
        <ThemeLink asChild size="2">
          <Link
            href={buildChatHref({
              agentId,
              projectId,
              conversationId: origin.conversationId,
              messageId: origin.atMessageId,
            })}
            style={{ overflowWrap: 'anywhere' }}
          >
            {origin.title || t('chat.untitledChat')}
          </Link>
        </ThemeLink>
      )}
    </Flex>
  );
}

/** The line a fork shows after the last message it copied: everything above is the copy, what follows is new. */
export function ForkOriginLine() {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="3"
      data-testid="fork-origin-line"
      style={{ color: 'var(--slate-11)', marginTop: 'var(--space-6)' }}
    >
      <Box aria-hidden="true" style={RULE_STYLE} />
      <Flex align="center" justify="center" gap="1" wrap="wrap" style={{ minWidth: 0 }}>
        <MaterialIcon name="call_split" size={14} color="var(--slate-11)" />
        <Text size="1">{t('chat.copiedUpToHere')}</Text>
      </Flex>
      <Box aria-hidden="true" style={RULE_STYLE} />
    </Flex>
  );
}

/** The line the original shows after the message a chat was forked at, linking to each fork made there. */
export function ForkedIntoLine({
  forks,
  agentId,
  projectId,
}: {
  forks: ForkChild[];
  agentId: string | null;
  projectId: string | null;
}) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="3"
      data-testid="forked-into-line"
      style={{ color: 'var(--slate-11)', marginTop: 'var(--space-6)' }}
    >
      <Box aria-hidden="true" style={RULE_STYLE} />
      <Flex align="center" justify="center" gap="2" wrap="wrap" style={{ minWidth: 0 }}>
        <MaterialIcon name="call_split" size={14} color="var(--slate-11)" />
        <Text size="1">{t('chat.forkedHere')} ·</Text>
        {forks.map((fork) => (
          <ThemeLink asChild size="1" key={fork.conversationId}>
            <Link
              href={buildChatHref({ agentId, projectId, conversationId: fork.conversationId })}
              style={{ overflowWrap: 'anywhere' }}
            >
              {fork.title || t('chat.untitledChat')}
            </Link>
          </ThemeLink>
        ))}
      </Flex>
      <Box aria-hidden="true" style={RULE_STYLE} />
    </Flex>
  );
}
