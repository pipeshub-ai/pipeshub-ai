'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, Link, Separator } from '@radix-ui/themes';
import { useDemoDataStatus } from '@/app/(main)/workspace/connectors/demo-data/use-demo-data';
import { useDemoSwitch } from '@/app/(main)/workspace/connectors/demo-data/use-demo-switch';
import { SettingsSection } from './settings-section';

export interface DemoDataSectionProps {
  isAdmin: boolean;
}

const hintStyle: React.CSSProperties = {
  color: 'var(--slate-11)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300,
};

function SwitchRow({ label, hint, checked, disabled, onChange }: {
  label: string;
  hint: React.ReactNode;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <Flex align="center" justify="between" gap="4" style={{ width: '100%' }}>
      <Box style={{ flex: 1 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>{label}</Text>
        <Text size="1" style={hintStyle}>{hint}</Text>
      </Box>
      <Switch aria-label={label} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </Flex>
  );
}

/** Each person's switch for the Acme Corp demo data, and an admin's for everyone; shown only while the demo exists. */
export function DemoDataSection({ isAdmin }: DemoDataSectionProps) {
  const { t } = useTranslation();
  const status = useDemoDataStatus();
  const { setInclude, setEnabledForEveryone, busy } = useDemoSwitch();

  if (!status?.hasDemo) return null;

  let personalHint: React.ReactNode = t('workspace.profile.demoData.followingDefault');
  if (status.offForEveryone) {
    personalHint = t('workspace.profile.demoData.offForEveryone');
  } else if (status.chosen !== null) {
    personalHint = (
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
    );
  }

  return (
    <Box style={{ marginBottom: 'var(--space-5)' }}>
      <SettingsSection title={t('workspace.profile.demoData.title')} description={t('workspace.profile.demoData.description')}>
        <SwitchRow
          label={t('workspace.profile.demoData.label')}
          hint={personalHint}
          checked={status.include}
          disabled={busy || status.offForEveryone}
          onChange={(checked) => void setInclude(checked)}
        />
        {isAdmin && (
          <>
            <Separator size="4" />
            <SwitchRow
              label={t('workspace.profile.demoData.everyoneLabel')}
              hint={t('workspace.profile.demoData.everyoneDescription')}
              checked={!status.offForEveryone}
              disabled={busy}
              onChange={(checked) => void setEnabledForEveryone(checked)}
            />
          </>
        )}
      </SettingsSection>
    </Box>
  );
}
