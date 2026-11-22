'use client';

import { useTranslation } from 'react-i18next';
import { EnterprisePlaceholderPage } from '@/app/(main)/workspace/components/enterprise-placeholder-page';
import { ENTERPRISE_GLIMPSES } from '@/app/(main)/workspace/components/enterprise-glimpses';

/**
 * Settings pages only the Enterprise Edition has. They live under /workspace/enterprise/ because
 * the Enterprise Edition owns /workspace/usage and the rest. `section` is where the sidebar lists
 * each one, locked: right under Groups, or in the Usage & credits or Security section.
 */
export const ENTERPRISE_FEATURES = {
  scim: {
    section: 'overview',
    icon: 'sync_alt',
    route: '/workspace/enterprise/scim',
    labelKey: 'workspace.sidebar.nav.scim',
    headingKey: 'workspace.enterprise.scim.heading',
    messageKey: 'workspace.enterprise.scim.message',
  },
  // One page for the Enterprise usage, credits, billing and credit settings pages.
  usage: {
    section: 'usageAndCredits',
    icon: 'paid',
    route: '/workspace/enterprise/usage',
    labelKey: 'workspace.sidebar.nav.usage',
    headingKey: 'workspace.enterprise.usage.heading',
    messageKey: 'workspace.enterprise.usage.message',
  },
  auditLogs: {
    section: 'security',
    icon: 'history',
    route: '/workspace/enterprise/audit-logs',
    labelKey: 'workspace.sidebar.nav.auditLogs',
    headingKey: 'workspace.enterprise.auditLogs.heading',
    messageKey: 'workspace.enterprise.auditLogs.message',
  },
} as const;

export type EnterpriseFeature = keyof typeof ENTERPRISE_FEATURES;
export type EnterpriseSection = (typeof ENTERPRISE_FEATURES)[EnterpriseFeature]['section'];

function EnterpriseFeaturePage({ feature }: { feature: EnterpriseFeature }) {
  const { t } = useTranslation();
  const { labelKey, headingKey, messageKey } = ENTERPRISE_FEATURES[feature];

  return (
    <EnterprisePlaceholderPage
      title={t(labelKey)}
      heading={t(headingKey)}
      message={t(messageKey)}
      contactLabel={t('workspace.enterprise.contact')}
      glimpse={ENTERPRISE_GLIMPSES[feature]}
    />
  );
}

export function ScimPlaceholderPage() {
  return <EnterpriseFeaturePage feature="scim" />;
}

export function UsagePlaceholderPage() {
  return <EnterpriseFeaturePage feature="usage" />;
}

export function AuditLogsPlaceholderPage() {
  return <EnterpriseFeaturePage feature="auditLogs" />;
}
