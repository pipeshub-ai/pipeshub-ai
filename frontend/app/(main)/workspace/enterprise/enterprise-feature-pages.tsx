'use client';

import { useTranslation } from 'react-i18next';
import { EnterprisePlaceholderPage } from '@/app/(main)/workspace/components/enterprise-placeholder-page';
import { ENTERPRISE_GLIMPSES } from '@/app/(main)/workspace/components/enterprise-glimpses';

/**
 * Settings pages only the Enterprise Edition has. They live under /workspace/enterprise/ because
 * the Enterprise Edition owns /workspace/usage, /workspace/credits and the rest. `section` is the
 * sidebar section each one is listed in, locked.
 */
export const ENTERPRISE_FEATURES = {
  scim: {
    section: 'security',
    icon: 'sync_alt',
    route: '/workspace/enterprise/scim',
    labelKey: 'workspace.sidebar.nav.scim',
    headingKey: 'workspace.enterprise.scim.heading',
    messageKey: 'workspace.enterprise.scim.message',
  },
  auditLogs: {
    section: 'security',
    icon: 'history',
    route: '/workspace/enterprise/audit-logs',
    labelKey: 'workspace.sidebar.nav.auditLogs',
    headingKey: 'workspace.enterprise.auditLogs.heading',
    messageKey: 'workspace.enterprise.auditLogs.message',
  },
  usage: {
    section: 'usageAndCredits',
    icon: 'paid',
    route: '/workspace/enterprise/usage',
    labelKey: 'workspace.sidebar.nav.usage',
    headingKey: 'workspace.enterprise.usage.heading',
    messageKey: 'workspace.enterprise.usage.message',
  },
  credits: {
    section: 'usageAndCredits',
    icon: 'savings',
    route: '/workspace/enterprise/credits',
    labelKey: 'workspace.sidebar.nav.credits',
    headingKey: 'workspace.enterprise.credits.heading',
    messageKey: 'workspace.enterprise.credits.message',
  },
  billing: {
    section: 'usageAndCredits',
    icon: 'credit_card',
    route: '/workspace/enterprise/billing',
    labelKey: 'workspace.sidebar.nav.billing',
    headingKey: 'workspace.enterprise.billing.heading',
    messageKey: 'workspace.enterprise.billing.message',
  },
  creditSettings: {
    section: 'usageAndCredits',
    icon: 'tune',
    route: '/workspace/enterprise/credit-settings',
    labelKey: 'workspace.sidebar.nav.creditSettings',
    headingKey: 'workspace.enterprise.creditSettings.heading',
    messageKey: 'workspace.enterprise.creditSettings.message',
  },
} as const;

export type EnterpriseFeature = keyof typeof ENTERPRISE_FEATURES;

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

export function AuditLogsPlaceholderPage() {
  return <EnterpriseFeaturePage feature="auditLogs" />;
}

export function UsagePlaceholderPage() {
  return <EnterpriseFeaturePage feature="usage" />;
}

export function CreditsPlaceholderPage() {
  return <EnterpriseFeaturePage feature="credits" />;
}

export function BillingPlaceholderPage() {
  return <EnterpriseFeaturePage feature="billing" />;
}

export function CreditSettingsPlaceholderPage() {
  return <EnterpriseFeaturePage feature="creditSettings" />;
}
