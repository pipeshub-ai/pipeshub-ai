'use client';

import { useTranslation } from 'react-i18next';
import { EnterprisePlaceholderPage } from '@/app/(main)/workspace/components/enterprise-placeholder-page';
import { ENTERPRISE_GLIMPSES } from '@/app/(main)/workspace/components/enterprise-glimpses';

/**
 * Settings pages only the Enterprise Edition has, each listed locked in the sidebar where
 * `section` says: right under People, or in the Usage & credits or Security section. Groups and
 * Tenants keep the routes the Enterprise Edition uses, which `config-pages` hands to its own
 * pages; the rest live under /workspace/enterprise/ because the Enterprise Edition owns
 * /workspace/usage and the others.
 */
export const ENTERPRISE_FEATURES = {
  groups: {
    section: 'overview',
    icon: 'group',
    route: '/workspace/groups',
    labelKey: 'workspace.sidebar.nav.groups',
    titleKey: 'workspace.groups.title',
    subtitleKey: 'workspace.groups.subtitle',
    headingKey: 'workspace.groups.enterpriseTitle',
    messageKey: 'workspace.groups.enterpriseMessage',
    benefitKeys: [
      'workspace.enterprise.groups.benefits.grantOnce',
      'workspace.enterprise.groups.benefits.teams',
      'workspace.enterprise.groups.benefits.builtIn',
    ],
  },
  tenants: {
    section: 'overview',
    icon: 'account_tree',
    route: '/workspace/tenants',
    labelKey: 'workspace.sidebar.nav.tenants',
    titleKey: 'workspace.tenants.title',
    subtitleKey: 'workspace.tenants.subtitle',
    headingKey: 'workspace.enterprise.tenants.heading',
    messageKey: 'workspace.enterprise.tenants.message',
    benefitKeys: [
      'workspace.enterprise.tenants.benefits.separate',
      'workspace.enterprise.tenants.benefits.inherit',
      'workspace.enterprise.tenants.benefits.manage',
    ],
  },
  scim: {
    section: 'overview',
    icon: 'sync_alt',
    route: '/workspace/enterprise/scim',
    labelKey: 'workspace.sidebar.nav.scim',
    headingKey: 'workspace.enterprise.scim.heading',
    messageKey: 'workspace.enterprise.scim.message',
    benefitKeys: [
      'workspace.enterprise.scim.benefits.providers',
      'workspace.enterprise.scim.benefits.offboarding',
      'workspace.enterprise.scim.benefits.noManualWork',
    ],
  },
  // One page for the Enterprise usage, credits, billing and credit settings pages.
  usage: {
    section: 'usageAndCredits',
    icon: 'paid',
    route: '/workspace/enterprise/usage',
    labelKey: 'workspace.sidebar.nav.usage',
    headingKey: 'workspace.enterprise.usage.heading',
    messageKey: 'workspace.enterprise.usage.message',
    benefitKeys: [
      'workspace.enterprise.usage.benefits.breakdown',
      'workspace.enterprise.usage.benefits.billing',
      'workspace.enterprise.usage.benefits.alerts',
    ],
  },
  auditLogs: {
    section: 'security',
    icon: 'history',
    route: '/workspace/enterprise/audit-logs',
    labelKey: 'workspace.sidebar.nav.auditLogs',
    headingKey: 'workspace.enterprise.auditLogs.heading',
    messageKey: 'workspace.enterprise.auditLogs.message',
    benefitKeys: [
      'workspace.enterprise.auditLogs.benefits.everyEvent',
      'workspace.enterprise.auditLogs.benefits.filters',
      'workspace.enterprise.auditLogs.benefits.alerts',
    ],
  },
} as const;

export type EnterpriseFeature = keyof typeof ENTERPRISE_FEATURES;
export type EnterpriseSection = (typeof ENTERPRISE_FEATURES)[EnterpriseFeature]['section'];

const RELATED_COUNT = 3;

/** The other Enterprise pages a placeholder points to, in sidebar order. */
export function relatedEnterpriseFeatures(feature: EnterpriseFeature): EnterpriseFeature[] {
  return (Object.keys(ENTERPRISE_FEATURES) as EnterpriseFeature[])
    .filter((other) => other !== feature)
    .slice(0, RELATED_COUNT);
}

function EnterpriseFeaturePage({ feature }: { feature: EnterpriseFeature }) {
  const { t } = useTranslation();
  const entry = ENTERPRISE_FEATURES[feature];
  const titleKey = 'titleKey' in entry ? entry.titleKey : entry.labelKey;
  const subtitleKey = 'subtitleKey' in entry ? entry.subtitleKey : undefined;

  return (
    <EnterprisePlaceholderPage
      feature={feature}
      title={t(titleKey)}
      subtitle={subtitleKey ? t(subtitleKey) : undefined}
      heading={t(entry.headingKey)}
      message={t(entry.messageKey)}
      benefits={entry.benefitKeys.map((key) => t(key))}
      related={relatedEnterpriseFeatures(feature).map((other) => ({
        label: t(ENTERPRISE_FEATURES[other].labelKey),
        href: `${ENTERPRISE_FEATURES[other].route}/`,
      }))}
      glimpse={ENTERPRISE_GLIMPSES[feature]}
    />
  );
}

export function GroupsPlaceholderPage() {
  return <EnterpriseFeaturePage feature="groups" />;
}

export function TenantsPlaceholderPage() {
  return <EnterpriseFeaturePage feature="tenants" />;
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
