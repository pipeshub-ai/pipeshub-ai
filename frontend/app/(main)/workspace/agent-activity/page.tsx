'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, Heading, Select, Text } from '@radix-ui/themes';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useUserDirectoryEntry } from '@/lib/hooks/use-user-directory-entry';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '../components/settings-page-container';
import {
  AgentActivityApi,
  type AgentCallEvent,
  type AgentTokenKind,
  type StatusClass,
} from './api';

const MONO: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  wordBreak: 'break-all',
};

const ALL = 'all';
const TOKEN_KINDS: AgentTokenKind[] = ['pat', 'oauth', 'delegated'];
const STATUS_CLASSES: StatusClass[] = ['2xx', '4xx', '5xx'];

const KIND_LABEL: Record<AgentTokenKind, string> = {
  pat: 'workspace.agentActivity.kindPat',
  oauth: 'workspace.agentActivity.kindOauth',
  delegated: 'workspace.agentActivity.kindDelegated',
};

const statusColour = (code: number): 'green' | 'amber' | 'red' =>
  code >= 500 ? 'red' : code >= 400 ? 'amber' : 'green';

function formatWhen(value: string, locale?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function UserCell({ userId }: { userId: string }) {
  const user = useUserDirectoryEntry(userId);
  return (
    <Flex direction="column" style={{ minWidth: 0 }}>
      <Text size="2">{user?.name || user?.email || userId}</Text>
      {user?.name && user.email && (
        <Text size="1" style={{ color: 'var(--slate-10)', wordBreak: 'break-all' }}>
          {user.email}
        </Text>
      )}
    </Flex>
  );
}

export default function AgentActivityPage() {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const isMobile = useIsMobile();

  const [kind, setKind] = useState<AgentTokenKind | typeof ALL>(ALL);
  const [status, setStatus] = useState<StatusClass | typeof ALL>(ALL);
  const [items, setItems] = useState<AgentCallEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | undefined>();
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A failed older page keeps the rows already shown; the button then retries the same cursor.
  const [moreError, setMoreError] = useState<string | null>(null);
  // A page for the previous filter that lands late must not be mixed into the current list.
  const loadSeq = useRef(0);

  const load = useCallback(
    async (cursor?: string) => {
      const seq = ++loadSeq.current;
      if (cursor) setIsLoadingMore(true);
      else setIsLoading(true);
      setError(null);
      setMoreError(null);
      try {
        const page = await AgentActivityApi.list({
          ...(kind !== ALL && { tokenKind: kind }),
          ...(status !== ALL && { status }),
          ...(cursor && { cursor }),
        });
        if (seq !== loadSeq.current) return;
        setItems((prev) => (cursor ? [...prev, ...page.items] : page.items));
        setNextCursor(page.nextCursor);
      } catch (err: unknown) {
        if (seq !== loadSeq.current) return;
        const message = getUserFacingErrorMessage(err, t('workspace.agentActivity.loadError'));
        if (cursor) setMoreError(message);
        else setError(message);
      } finally {
        if (seq === loadSeq.current) {
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [kind, status, t]
  );

  // The sidebar hides this page from members, but the URL still opens it. A profile that failed
  // to load leaves isAdmin null; General is open to everyone, so that goes there too.
  useEffect(() => {
    if (isProfileInitialized && isAdmin !== true) router.replace('/workspace/general');
  }, [isProfileInitialized, isAdmin, router]);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin !== true) return;
    void load();
  }, [isProfileInitialized, isAdmin, load]);

  if (!isProfileInitialized || isAdmin !== true) return null;

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingInline: pagePaddingX(40),
        boxSizing: 'border-box',
        minWidth: 0,
      }}
    >
      <Flex
        justify="between"
        align="end"
        wrap="wrap"
        gap="3"
        style={{ paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP, paddingBottom: 'var(--space-5)' }}
      >
        <Flex direction="column" gap="2" style={{ flex: '1 1 240px', minWidth: 0 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.agentActivity.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.agentActivity.subtitle')}
          </Text>
        </Flex>
        <Flex gap="2" wrap="wrap">
          <Select.Root value={kind} onValueChange={(v) => setKind(v as AgentTokenKind | typeof ALL)}>
            <Select.Trigger aria-label={t('workspace.agentActivity.filterKind')} style={{ minHeight: 36 }} />
            <Select.Content position="popper">
              <Select.Item value={ALL}>{t('workspace.agentActivity.allKinds')}</Select.Item>
              {TOKEN_KINDS.map((k) => (
                <Select.Item key={k} value={k}>
                  {t(KIND_LABEL[k])}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
          <Select.Root value={status} onValueChange={(v) => setStatus(v as StatusClass | typeof ALL)}>
            <Select.Trigger aria-label={t('workspace.agentActivity.filterStatus')} style={{ minHeight: 36 }} />
            <Select.Content position="popper">
              <Select.Item value={ALL}>{t('workspace.agentActivity.allStatuses')}</Select.Item>
              {STATUS_CLASSES.map((s) => (
                <Select.Item key={s} value={s}>
                  {s}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
        </Flex>
      </Flex>

      <Flex direction="column" style={{ flex: 1, minHeight: 0, width: '100%' }}>
        {isLoading && (
          <Flex align="center" justify="center" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <LottieLoader variant="loader" size={48} showLabel label={t('common.loading')} />
          </Flex>
        )}

        {!isLoading && error && (
          <Flex direction="column" align="center" justify="center" gap="3" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center' }}>
              {error}
            </Text>
            <Button size="2" variant="soft" onClick={() => void load()}>
              {t('workspace.agentActivity.retry')}
            </Button>
          </Flex>
        )}

        {!isLoading && !error && items.length === 0 && (
          <Flex direction="column" align="center" justify="center" gap="2" style={{ flex: 1, padding: 'var(--space-6)' }}>
            <Text size="3" weight="medium">
              {t('workspace.agentActivity.emptyTitle')}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-11)', textAlign: 'center' }}>
              {t('workspace.agentActivity.emptyDescription')}
            </Text>
          </Flex>
        )}

        {!isLoading && !error && items.length > 0 && (
          <Flex
            direction="column"
            gap="3"
            style={{ flex: 1, minHeight: 0, overflow: 'auto', paddingTop: 'var(--space-2)', paddingBottom: 'var(--space-4)' }}
          >
            <Flex
              direction="column"
              style={{ border: '1px solid var(--olive-3)', borderRadius: 'var(--radius-3)', overflow: 'hidden', flexShrink: 0 }}
            >
              <Flex
                gap="3"
                style={{
                  padding: 'var(--space-3) var(--space-4)',
                  backgroundColor: 'var(--olive-2)',
                  borderBottom: '1px solid var(--olive-3)',
                }}
              >
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 2 }}>
                  {t('workspace.agentActivity.columnUser')}
                </Text>
                {!isMobile && (
                  <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 2 }}>
                    {t('workspace.agentActivity.columnToken')}
                  </Text>
                )}
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 3 }}>
                  {t('workspace.agentActivity.columnRequest')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1 }}>
                  {t('workspace.agentActivity.columnStatus')}
                </Text>
                <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', flex: 1, textAlign: 'right' }}>
                  {t('workspace.agentActivity.columnTime')}
                </Text>
              </Flex>

              {items.map((call) => (
                <Flex
                  key={call.id}
                  align="center"
                  gap="3"
                  style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--olive-3)' }}
                >
                  <Flex style={{ flex: 2, minWidth: 0 }}>
                    <UserCell userId={call.userId} />
                  </Flex>
                  {!isMobile && (
                    <Flex direction="column" gap="1" style={{ flex: 2, minWidth: 0 }}>
                      <Flex>
                        <Badge color="gray" size="1">
                          {t(KIND_LABEL[call.tokenKind] ?? call.tokenKind)}
                        </Badge>
                      </Flex>
                      {call.userAgent && (
                        <Text size="1" style={{ color: 'var(--slate-10)', ...MONO }}>
                          {call.userAgent}
                        </Text>
                      )}
                    </Flex>
                  )}
                  <Flex direction="column" gap="1" style={{ flex: 3, minWidth: 0 }}>
                    <Text size="1" style={MONO}>
                      {call.method} {call.route}
                    </Text>
                    {call.mcpTool && (
                      <Text size="1" style={{ color: 'var(--slate-10)', ...MONO }}>
                        {call.mcpTool}
                      </Text>
                    )}
                  </Flex>
                  <Flex direction="column" gap="1" align="start" style={{ flex: 1, minWidth: 0 }}>
                    <Badge color={statusColour(call.statusCode)} size="1">
                      {call.statusCode}
                    </Badge>
                    {call.scopeDenied && (
                      <Text size="1" style={{ color: 'var(--amber-11)' }}>
                        {t('workspace.agentActivity.scopeDenied')}
                      </Text>
                    )}
                    {call.count > 1 && (
                      <Text size="1" style={{ color: 'var(--slate-10)' }}>
                        {t('workspace.agentActivity.rolledUp', { calls: call.count })}
                      </Text>
                    )}
                  </Flex>
                  <Text size="1" style={{ color: 'var(--slate-10)', flex: 1, textAlign: 'right' }}>
                    {formatWhen(call.createdAt, i18n.language)}
                  </Text>
                </Flex>
              ))}
            </Flex>

            {nextCursor && (
              <Flex direction="column" align="center" gap="2">
                {moreError && (
                  <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center' }}>
                    {moreError}
                  </Text>
                )}
                <Button
                  size="2"
                  variant="soft"
                  className="touch-hit"
                  disabled={isLoadingMore}
                  onClick={() => void load(nextCursor)}
                  style={{ cursor: 'pointer' }}
                >
                  {t(moreError ? 'workspace.agentActivity.retry' : 'workspace.agentActivity.loadMore')}
                </Button>
              </Flex>
            )}
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}
