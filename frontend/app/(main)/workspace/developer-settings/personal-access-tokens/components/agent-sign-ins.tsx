'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { AgentSignInApi, type AgentSignIn } from '../api';

/** The agents the user approved, each revocable. Renders nothing when there are none; a failed load offers a retry. */
export function AgentSignIns() {
  const { t, i18n } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [items, setItems] = useState<AgentSignIn[]>([]);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await AgentSignInApi.list());
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async (id: string) => {
    try {
      await AgentSignInApi.revoke(id);
      addToast({ variant: 'success', title: t('workspace.agentSignIns.revokedTitle'), duration: 3000 });
      void load();
    } catch {
      addToast({ variant: 'error', title: t('workspace.agentSignIns.errorTitle'), duration: 4000 });
    }
  };

  if (failed) {
    return (
      <Flex align="center" gap="3" style={{ paddingBottom: 'var(--space-5)' }}>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.agentSignIns.loadError')}
        </Text>
        <Button
          type="button"
          variant="soft"
          size="2"
          disabled={loading}
          onClick={() => void load()}
          style={{ cursor: 'pointer', minHeight: 44, minWidth: 44 }}
        >
          {t('action.tryAgain')}
        </Button>
      </Flex>
    );
  }
  if (items.length === 0) return null;

  return (
    <Flex direction="column" gap="2" style={{ paddingBottom: 'var(--space-5)' }}>
      <Heading size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {t('workspace.agentSignIns.title')}
      </Heading>
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('workspace.agentSignIns.subtitle')}
      </Text>
      <Flex direction="column" style={{ border: '1px solid var(--olive-3)', borderRadius: 'var(--radius-3)' }}>
        {items.map((item) => (
          <Flex
            key={item.id}
            align="center"
            justify="between"
            gap="3"
            style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--olive-3)' }}
          >
            <Flex direction="column" style={{ minWidth: 0 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
                {item.id}
              </Text>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {t('workspace.agentSignIns.columnApproved')}{' '}
                {new Date(item.created_at).toLocaleDateString(i18n.language, {
                  year: 'numeric',
                  month: 'short',
                  day: 'numeric',
                })}
              </Text>
            </Flex>
            <Button
              type="button"
              variant="soft"
              color="red"
              size="2"
              aria-label={t('workspace.agentSignIns.revokeAria', { id: item.id })}
              onClick={() => void revoke(item.id)}
              style={{ cursor: 'pointer', flexShrink: 0, minHeight: 44, minWidth: 44 }}
            >
              {t('workspace.agentSignIns.revoke')}
            </Button>
          </Flex>
        ))}
      </Flex>
    </Flex>
  );
}
