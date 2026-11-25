'use client';

import React, { useEffect, useState } from 'react';
import { Box, Button, Flex, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { Link } from '@/lib/navigation';
import { LabsApi } from '../labs/api';

/** Fired after the notice changed the settings, so an open Labs page reloads them. */
export const LABS_SETTINGS_CHANGED = 'pipeshub:labs-settings-changed';

/**
 * Tells org admins that the agent harness can be turned on (BETA): the operator runs it, but it is in beta and off by
 * default, so the org uses the classic chat until an admin turns on "Agent harness" in Labs. Shown while the org has
 * it off and no admin has answered; turning the flag on or off in Labs, or dismissing this, answers it for good (the
 * server records the answer). Members never see it: for them the product is the classic one.
 */
export function DurableDefaultNotice() {
  const { t } = useTranslation();
  const isAdmin = useUserStore(selectIsAdmin);
  const ready = useUserStore(selectIsProfileInitialized);
  const orgOff = useFeatureFlagsStore((s) => s.agentHarness?.reason === 'org_disabled');
  const [unanswered, setUnanswered] = useState(false);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!ready || isAdmin !== true || !orgOff) return;
    let live = true;
    LabsApi.getSettings()
      .then((settings) => {
        const notice = settings?.notices?.durableAgentDefault;
        if (live) setUnanswered(notice === undefined || notice === 'pending');
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [ready, isAdmin, orgOff]);

  if (!orgOff || !unanswered) return null;

  const dismiss = async () => {
    setSending(true);
    setFailed(false);
    try {
      await LabsApi.saveSettings({ notices: { durableAgentDefault: 'dismissed' } });
      setUnanswered(false);
      window.dispatchEvent(new Event(LABS_SETTINGS_CHANGED));
    } catch {
      setFailed(true);
    } finally {
      setSending(false);
    }
  };

  return (
    <Box style={{ padding: 'var(--space-4) var(--space-6) 0' }}>
      <Flex
        role="region"
        aria-labelledby="durable-default-notice-title"
        data-testid="durable-default-notice"
        direction="column"
        gap="2"
        style={{
          backgroundColor: 'var(--accent-a2)',
          border: '1px solid var(--accent-a5)',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-4)',
        }}
      >
        <Flex align="start" justify="between" gap="3">
          <Flex align="center" gap="2">
            <MaterialIcon name="science" size={18} color="var(--accent-11)" />
            <Text id="durable-default-notice-title" size="2" weight="bold">
              {t('workspace.labs.durableNotice.title')}
            </Text>
            <BetaBadge />
          </Flex>
          <IconButton
            variant="ghost"
            size="1"
            color="gray"
            disabled={sending}
            onClick={() => void dismiss()}
            aria-label={t('workspace.labs.durableNotice.dismiss')}
          >
            <MaterialIcon name="close" size={16} />
          </IconButton>
        </Flex>
        <Text size="2">{t('workspace.labs.durableNotice.body')}</Text>
        {/* What people will notice besides surviving restarts (UX3, R1 F18). */}
        <ul style={{ margin: 0, paddingInlineStart: 'var(--space-5)' }}>
          <li><Text size="2">{t('workspace.labs.durableNotice.changes.asks')}</Text></li>
          <li><Text size="2">{t('workspace.labs.durableNotice.changes.steering')}</Text></li>
          <li><Text size="2">{t('workspace.labs.durableNotice.changes.screens')}</Text></li>
          <li><Text size="2">{t('workspace.labs.durableNotice.changes.legacy')}</Text></li>
        </ul>
        {failed ? (
          <Text size="2" color="red" role="alert">
            {t('workspace.labs.durableNotice.failed')}
          </Text>
        ) : null}
        <Flex gap="2" wrap="wrap" style={{ marginTop: 'var(--space-1)' }}>
          <Button asChild>
            <Link href="/workspace/labs/">{t('workspace.labs.durableNotice.openLabs')}</Link>
          </Button>
        </Flex>
      </Flex>
    </Box>
  );
}
