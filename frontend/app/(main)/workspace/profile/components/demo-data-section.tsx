'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, Link } from '@radix-ui/themes';
import { useDemoDataStatus } from '@/app/(main)/workspace/connectors/demo-data/use-demo-data';
import { useDemoSwitch } from '@/app/(main)/workspace/connectors/demo-data/use-demo-switch';
import { SettingsSection } from './settings-section';

/** Each person's switch for the Acme Corp demo data; shown only while the demo exists. */
export function DemoDataSection() {
  const { t } = useTranslation();
  const status = useDemoDataStatus();
  const { setInclude, busy } = useDemoSwitch();

  if (!status?.hasDemo) return null;

  const label = t('workspace.profile.demoData.label');
  return (
    <Box style={{ marginBottom: 'var(--space-5)' }}>
    <SettingsSection title={t('workspace.profile.demoData.title')} description={t('workspace.profile.demoData.description')}>
      <Flex align="center" justify="between" gap="4" style={{ width: '100%' }}>
        <Box style={{ flex: 1 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
            {label}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300 }}>
            {status.chosen === null ? (
              t('workspace.profile.demoData.followingDefault')
            ) : (
              <>
                {t('workspace.profile.demoData.yourChoice')}{' '}
                <Link asChild size="1">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void setInclude(null)}
                    style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
                  >
                    {t('workspace.profile.demoData.useDefault')}
                  </button>
                </Link>
              </>
            )}
          </Text>
        </Box>
        <Switch
          aria-label={label}
          checked={status.include}
          disabled={busy}
          onCheckedChange={(checked) => void setInclude(checked)}
        />
      </Flex>
    </SettingsSection>
    </Box>
  );
}
