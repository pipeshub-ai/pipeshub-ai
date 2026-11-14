'use client';

import { useId, useState } from 'react';
import useSWR from 'swr';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Heading, Link, Text, TextField } from '@radix-ui/themes';
import { selectIsAdmin, useUserStore } from '@/lib/store/user-store';
import { useUserDirectoryEntry } from '@/lib/hooks/use-user-directory-entry';
import { ConnectorsApi } from '../../api';
import type { NotionPersonalTokenState } from '../../types';

const GUIDE_URL = 'https://developers.notion.com/guides/connections-and-tokens/personal-access-tokens';

/** Only a Notion team connector reads each person's own token; the server answers 404 for any other. */
export function isNotionTeamConnector(instance: { type?: string; scope?: string }): boolean {
  return instance.type?.toLowerCase() === 'notion' && instance.scope === 'team';
}

function useStatusText() {
  const { t, i18n } = useTranslation();
  return (entry: NotionPersonalTokenState) => {
    const k = (key: string, o?: Record<string, unknown>) => t(`workspace.connectors.notionAccess.status.${key}`, o);
    if (entry.status === 'LINKED') {
      return entry.linkedAt
        ? k('linkedOn', { when: new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' }).format(new Date(entry.linkedAt)) })
        : k('linked');
    }
    if (entry.status === 'FAILED') return entry.reason ? k('failedReason', { reason: entry.reason }) : k('failed');
    return k('notLinked');
  };
}

function LinkedUser({ entry }: { entry: NotionPersonalTokenState & { userId: string } }) {
  const statusText = useStatusText();
  const user = useUserDirectoryEntry(entry.userId);
  return (
    <li>
      <Flex direction="column">
        <Text size="2" weight="medium">{user?.name?.trim() || user?.email || entry.userId}</Text>
        <Text size="2" color={entry.status === 'FAILED' ? 'red' : 'gray'}>{statusText(entry)}</Text>
      </Flex>
    </li>
  );
}

/** "Your Notion access" on a Notion team connector: the caller links their own token; admins also see who linked one. */
export function NotionPersonalToken({ connectorId, connectorName }: { connectorId: string; connectorName?: string }) {
  const { t } = useTranslation();
  const k = (key: string, o?: Record<string, unknown>) => t(`workspace.connectors.notionAccess.${key}`, o);
  const uid = useId();
  const statusText = useStatusText();
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const own = useSWR(['notion-personal-token', connectorId], () => ConnectorsApi.getNotionPersonalToken(connectorId));
  const linked = useSWR(isAdmin ? ['notion-personal-tokens', connectorId] : null, () =>
    ConnectorsApi.listNotionPersonalTokens(connectorId)
  );
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);

  const change = async (call: () => Promise<NotionPersonalTokenState>) => {
    setBusy(true);
    try {
      await own.mutate(await call(), { revalidate: false });
      setToken('');
      void linked.mutate();
    } catch {
      // The API client already told the user what went wrong.
    } finally {
      setBusy(false);
    }
  };

  const entry = own.data;
  const pasted = token.trim();

  return (
    <Box asChild>
      <section aria-labelledby={`${uid}-h`} data-testid="notion-personal-token">
        <Flex direction="column" gap="3">
          <Heading as="h3" id={`${uid}-h`} size="3">
            {connectorName ? k('titleFor', { connector: connectorName }) : k('title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {k('intro')}{' '}
            <Link href={GUIDE_URL} target="_blank" rel="noopener noreferrer">{k('guide')}</Link>
          </Text>

          {own.isLoading ? (
            <Text size="2" role="status" style={{ color: 'var(--gray-11)' }}>{t('workspace.connectors.access.loading')}</Text>
          ) : own.error || !entry ? (
            <Flex align="center" gap="3" wrap="wrap" role="alert">
              <Text size="2" color="red">{k('loadError')}</Text>
              <Button size="2" variant="soft" style={{ minHeight: 44 }} onClick={() => void own.mutate()}>
                {t('workspace.connectors.access.retry')}
              </Button>
            </Flex>
          ) : (
            <>
              <Text size="2" color={entry.status === 'FAILED' ? 'red' : undefined} data-testid="notion-token-status">
                {statusText(entry)}
              </Text>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (pasted && !busy) void change(() => ConnectorsApi.linkNotionPersonalToken(connectorId, pasted));
                }}
              >
                <Flex direction="column" gap="2">
                  <Text as="label" htmlFor={`${uid}-t`} size="2" weight="medium">{k('tokenLabel')}</Text>
                  <Flex gap="2" wrap="wrap" align="center">
                    <Box style={{ flex: '1 1 220px' }}>
                      <TextField.Root
                        id={`${uid}-t`}
                        type="password"
                        autoComplete="off"
                        spellCheck={false}
                        value={token}
                        onChange={(e) => setToken(e.target.value)}
                        style={{ minHeight: 44 }}
                      />
                    </Box>
                    <Button type="submit" size="2" style={{ minHeight: 44 }} disabled={!pasted || busy} aria-busy={busy}>
                      {entry.status === 'LINKED' ? k('replace') : k('link')}
                    </Button>
                    {entry.status !== 'NOT_LINKED' && (
                      <Button
                        type="button"
                        size="2"
                        variant="soft"
                        color="red"
                        style={{ minHeight: 44 }}
                        disabled={busy}
                        onClick={() => void change(() => ConnectorsApi.unlinkNotionPersonalToken(connectorId))}
                      >
                        {k('remove')}
                      </Button>
                    )}
                  </Flex>
                  <Text size="1" style={{ color: 'var(--gray-11)' }}>{k('nextSync')}</Text>
                </Flex>
              </form>
            </>
          )}

          {isAdmin && (
            <Flex direction="column" gap="2">
              <Text size="2" weight="medium">{k('linkedUsers')}</Text>
              {linked.error ? (
                <Text size="2" color="red" role="alert">{k('loadError')}</Text>
              ) : linked.data?.length === 0 ? (
                <Text size="2" style={{ color: 'var(--gray-11)' }}>{k('nobodyLinked')}</Text>
              ) : (
                <Flex asChild direction="column" gap="2" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  <ul>
                    {(linked.data ?? []).map((u) => <LinkedUser key={u.userId} entry={u} />)}
                  </ul>
                </Flex>
              )}
            </Flex>
          )}
        </Flex>
      </section>
    </Box>
  );
}

/** Members cannot open a team connector's page, so their Notion panels live on their own connectors page. */
export function NotionTeamAccess() {
  const { data } = useSWR(['notion-team-connectors'], () =>
    ConnectorsApi.getActiveConnectors('team', 1, 100, { suppressErrorToast: true })
  );
  const notion = (data?.connectors ?? []).filter((c) => c._key && isNotionTeamConnector(c));
  if (notion.length === 0) return null;
  return (
    <Flex direction="column" gap="5" mb="5">
      {notion.map((c) => (
        <NotionPersonalToken key={c._key} connectorId={c._key as string} connectorName={c.name} />
      ))}
    </Flex>
  );
}
