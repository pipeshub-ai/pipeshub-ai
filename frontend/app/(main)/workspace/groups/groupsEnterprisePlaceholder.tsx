'use client';

import { useTranslation } from 'react-i18next';
import { EnterprisePlaceholderPage } from '@/app/(main)/workspace/components/enterprise-placeholder-page';
import { ENTERPRISE_GLIMPSES } from '@/app/(main)/workspace/components/enterprise-glimpses';

/**
 * Open-source Groups page — full group management is an Enterprise Edition feature.
 * Sidebar / bookmarks still land here so the upgrade message is visible.
 */
export default function GroupsEnterprisePlaceholder() {
  const { t } = useTranslation();

  return (
    <EnterprisePlaceholderPage
      title={t('workspace.groups.title')}
      subtitle={t('workspace.groups.subtitle')}
      heading={t('workspace.groups.enterpriseTitle', 'Group permissions are available in the Enterprise Edition')}
      message={t('workspace.groups.enterpriseMessage', 'Upgrade to the Enterprise Edition to use group permissions.')}
      contactLabel={t('workspace.enterprise.contact')}
      glimpse={ENTERPRISE_GLIMPSES.groups}
    />
  );
}
