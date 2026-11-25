'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Heading, Table, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { BotsApi } from '../api';
import type { SlackIdentityLink } from '../types';

/**
 * Who each Slack person acts as in PipesHub. A person links once by signing in from the link the bot sends them
 * privately; an admin can see every link and remove one (the person then links again on their next message).
 */
export function SlackIdentityLinks() {
  const { t } = useTranslation();
  const [links, setLinks] = useState<SlackIdentityLink[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLinks(await BotsApi.getSlackIdentityLinks());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (link: SlackIdentityLink) => {
    const key = `${link.teamId}/${link.slackUserId}`;
    setRemoving(key);
    try {
      await BotsApi.revokeSlackIdentityLink(link.teamId, link.slackUserId);
      toast.success(t('workspace.bots.identityLinks.removed'));
      await load();
    } catch {
      toast.error(t('workspace.bots.identityLinks.removeFailed'));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <Flex direction="column" gap="2" style={{ width: '100%' }} data-testid="slack-identity-links">
      <Heading size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {t('workspace.bots.identityLinks.title')}
      </Heading>
      <Text size="2" style={{ color: 'var(--gray-11)' }}>
        {t('workspace.bots.identityLinks.subtitle')}
      </Text>
      {failed ? (
        <Text size="2" color="red">
          {t('workspace.bots.identityLinks.loadFailed')}
        </Text>
      ) : links === null ? null : links.length === 0 ? (
        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          {t('workspace.bots.identityLinks.empty')}
        </Text>
      ) : (
        <Table.Root size="1" variant="surface">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>{t('workspace.bots.identityLinks.slackPerson')}</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>{t('workspace.bots.identityLinks.account')}</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>{t('workspace.bots.identityLinks.linkedAt')}</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell />
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {links.map((link) => {
              const key = `${link.teamId}/${link.slackUserId}`;
              return (
                <Table.Row key={key}>
                  <Table.Cell>
                    {link.slackName ?? link.slackUserId}
                    <Text as="div" size="1" style={{ color: 'var(--gray-11)' }}>
                      {link.teamId} · {link.slackUserId}
                    </Text>
                  </Table.Cell>
                  <Table.Cell>
                    {link.active ? (
                      link.fullName ?? link.email
                    ) : (
                      <Text color="amber">{t('workspace.bots.identityLinks.inactive')}</Text>
                    )}
                    {link.active && link.fullName && link.email ? (
                      <Text as="div" size="1" style={{ color: 'var(--gray-11)' }}>
                        {link.email}
                      </Text>
                    ) : null}
                  </Table.Cell>
                  <Table.Cell>{new Date(link.linkedAt).toLocaleDateString()}</Table.Cell>
                  <Table.Cell>
                    <Button
                      size="1"
                      variant="soft"
                      color="red"
                      disabled={removing === key}
                      onClick={() => void remove(link)}
                    >
                      {t('workspace.bots.identityLinks.remove')}
                    </Button>
                  </Table.Cell>
                </Table.Row>
              );
            })}
          </Table.Body>
        </Table.Root>
      )}
    </Flex>
  );
}
