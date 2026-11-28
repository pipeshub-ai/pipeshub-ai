'use client';

import { useTranslation } from 'react-i18next';
import { Box, Button } from '@radix-ui/themes';
import { Link } from '@/lib/navigation';
import { useEnterprisePrompt } from '@/lib/hooks/use-enterprise-prompt';
import { ENTERPRISE_OVERVIEW_ROUTE } from '@/lib/constants/enterprise';
import { SettingsRow, SettingsSection } from '@/app/(main)/workspace/components';

/** Which edition this is, on General settings, with the way to what the Enterprise Edition adds. */
export function WorkspaceEditionSection() {
  const { t } = useTranslation();
  const show = useEnterprisePrompt();
  if (!show) return null;

  return (
    <Box style={{ marginBottom: 'var(--space-5)' }}>
      <SettingsSection title={t('workspace.general.edition.title')}>
        <SettingsRow label={t('workspace.general.edition.name')} description={t('workspace.general.edition.description')}>
          <Button asChild size="2" variant="soft" className="touch-hit">
            <Link href={`${ENTERPRISE_OVERVIEW_ROUTE}/`}>{t('workspace.enterprise.seeWhatsInEnterprise')}</Link>
          </Button>
        </SettingsRow>
      </SettingsSection>
    </Box>
  );
}
