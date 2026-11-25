'use client';

import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Heading, Text } from '@radix-ui/themes';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { MyAiAccountsNotice, MyAiAccountsSections } from '@/config';
import { selectEngineRunsEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { SettingsSection } from '../../components/settings-section';
import { offeringKey } from '../access-copy';
import { useMyAccountsStore } from '../store';
import { AccessRulesTable } from './access-rules-table';
import { LoadState } from './load-state';
import { EngineSignInSection } from './engine-signin-section';
import { MyCodingEngine } from './my-coding-engine';

/** "My AI accounts": the person's own Claude sign-in, their coding engine, and what the org lets them use. */
export function MyAiAccounts() {
  const { t } = useTranslation();
  const engineRuns = useFeatureFlagsStore(selectEngineRunsEnabled);
  const { access, loadAccess } = useMyAccountsStore();

  useEffect(() => {
    void loadAccess();
  }, [loadAccess]);

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <Box style={{ padding: '64px var(--workspace-gutter, 100px)', paddingBottom: 80, maxWidth: 1100 }}>
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Flex align="center" gap="2">
            <Heading as="h1" size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('aiAccounts.my.title')}
            </Heading>
            <BetaBadge />
          </Flex>
          <Text size="2" as="p" style={{ color: 'var(--slate-11)', marginTop: 'var(--space-1)' }}>
            {t('aiAccounts.my.subtitle')}
          </Text>
          <MyAiAccountsNotice />
        </Box>

        <Flex direction="column" gap="5">
          {engineRuns && <EngineSignInSection />}

          <MyAiAccountsSections />

          <MyCodingEngine />

          <SettingsSection title={t('aiAccounts.my.accessTitle')} description={t('aiAccounts.my.accessDescription')}>
            <LoadState load={access} onRetry={() => void loadAccess()}>
              {(view) => (
                <Flex direction="column" gap="3">
                  <Text size="2" style={{ color: 'var(--slate-11)' }}>
                    {t('aiAccounts.common.offering', { offering: t(offeringKey(view.offering)) })}
                    {view.version ? ` · ${t('aiAccounts.common.rulesVersion', { version: view.version })}` : ''}
                  </Text>
                  <AccessRulesTable rules={view.rules} label={t('aiAccounts.my.accessTitle')} />
                </Flex>
              )}
            </LoadState>
          </SettingsSection>
        </Flex>
      </Box>
    </Box>
  );
}
