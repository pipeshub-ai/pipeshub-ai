'use client';

import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Box, Flex, Heading, Text } from '@radix-ui/themes';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { AiAccessControls, AiAccessEnginesExtras, AiAccessExtras } from '@/config';
import { SettingsSection } from '../../components/settings-section';
import { OrgCodingEngine } from './org-coding-engine';

/** The org's AI settings: its default coding engine, then any settings cards bound after it. Admins only. */
export function OrgAiAccess() {
  const { t } = useTranslation();
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) router.replace('/workspace/general');
  }, [isProfileInitialized, isAdmin, router]);

  if (!isProfileInitialized || isAdmin === false) return null;

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <Box style={{ padding: '64px var(--workspace-gutter, 100px)', paddingBottom: 80, maxWidth: 1100 }}>
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Flex align="center" gap="2">
            <Heading as="h1" size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('aiAccounts.org.title')}
            </Heading>
            <BetaBadge />
          </Flex>
          <Text size="2" as="p" style={{ color: 'var(--slate-11)', marginTop: 'var(--space-1)' }}>
            {t('aiAccounts.org.subtitle')}
          </Text>
        </Box>
        <Flex direction="column" gap="5">
          <SettingsSection title={t('aiAccounts.codingEngine.title')} description={t('aiAccounts.codingEngine.orgDescription')}>
            <OrgCodingEngine />
          </SettingsSection>
          <AiAccessControls />
          <AiAccessEnginesExtras />
          <AiAccessExtras />
        </Flex>
      </Box>
    </Box>
  );
}
