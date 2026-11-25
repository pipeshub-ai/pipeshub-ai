'use client';

import { useMemo } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { touchTarget } from '@/app/components/ui/responsive';
import { toast } from '@/lib/store/toast-store';
import { useUserStore } from '@/lib/store/user-store';
import { grantViews, type GrantView } from '@/lib/harness-jobs/jobs-list';
import type { Words } from '@/lib/harness-jobs/words';
import { useChatGrantsStore } from './chat-grants-store';
import { useChatGrants } from './use-chat-grants';

/**
 * The rows of "Permissions in this chat" (design: the Context column's grants row; ADR-GRANT-01 D8): each "Allow for
 * this chat" in words, with Remove. Removing takes it back on every open run of the chat and every later one, so the
 * next such call asks again; calls it already answered stay answered.
 */
export function ChatGrantsList({ isMobile }: { isMobile: boolean }) {
  const { t, i18n } = useTranslation();
  const { session, owner, shared, grants } = useChatGrants();
  const me = useUserStore((s) => s.profile?.userId ?? null);
  const removing = useChatGrantsStore((s) => (session ? (s.removing[session.sessionId] ?? null) : null));
  const words = useMemo<Words>(() => (key, vars) => t(key, vars) as string, [t]);
  const views = useMemo(() => grantViews(grants, words, i18n.language, { me, shared }), [grants, words, i18n.language, me, shared]);

  if (!session) return null;

  const remove = async (grant: GrantView) => {
    const outcome = await useChatGrantsStore.getState().revoke(session, grant.id);
    if (outcome === 'removed') toast.success(t('ui.grants.removed', { name: grant.title }), { description: t('ui.grants.removedBody') });
    else toast.error(t('ui.grants.removeFailed'));
  };

  if (views.length === 0) {
    // gray-11, not 10: on the popover's panel colour gray-10 text is under WCAG AA's 4.5:1 (axe, BGUI-8).
    return (
      <Text as="p" size="2" data-testid="chat-grants-empty" style={{ color: 'var(--gray-11)', padding: 'var(--space-2) 0' }}>
        {t('ui.grants.empty')}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="1" role="list" aria-label={t('ui.grants.title')}>
      {views.map((g) => (
        <Flex key={g.id} role="listitem" align="center" gap="3" data-testid="chat-grant" style={{ minHeight: isMobile ? 52 : 44, padding: 'var(--space-1) 0' }}>
          <MaterialIcon name={g.declined ? 'block' : 'how_to_reg'} size={18} color="var(--gray-11)" />
          <Flex direction="column" style={{ flex: 1, minWidth: 0 }}>
            <Text size="2" style={{ color: 'var(--gray-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={g.title}>
              {g.title}
            </Text>
            <Text size="1" style={{ color: 'var(--gray-11)' }}>
              {g.detail}
            </Text>
          </Flex>
          {owner ? (
            <Button
              size={isMobile ? '2' : '1'}
              variant="outline"
              color="gray"
              disabled={removing !== null}
              aria-label={t('ui.grants.removeLabel', { name: g.title })}
              onClick={() => void remove(g)}
              style={{ margin: 0, flexShrink: 0, ...touchTarget(isMobile) }}
            >
              {t('ui.grants.remove')}
            </Button>
          ) : null}
        </Flex>
      ))}
    </Flex>
  );
}
