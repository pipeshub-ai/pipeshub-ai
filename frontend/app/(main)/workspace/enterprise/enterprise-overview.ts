import { ENTERPRISE_FEATURES, type EnterpriseFeature } from './enterprise-feature-pages';

export interface EnterpriseOverviewItem {
  /** Names its copy: `workspace.enterprise.overview.items.<key>.{name,description}`. */
  key: string;
  icon: string;
  /** The locked page that shows it, when there is one. */
  feature?: EnterpriseFeature;
}

export interface EnterpriseOverviewGroup {
  /** Names its heading: `workspace.enterprise.overview.groups.<key>`. */
  key: string;
  items: EnterpriseOverviewItem[];
}

const fromFeature = (key: string, feature: EnterpriseFeature): EnterpriseOverviewItem => ({
  key,
  icon: ENTERPRISE_FEATURES[feature].icon,
  feature,
});

/** Everything in the Enterprise Edition, grouped by what an admin is trying to do. */
export const ENTERPRISE_OVERVIEW_GROUPS: EnterpriseOverviewGroup[] = [
  {
    key: 'scale',
    items: [fromFeature('tenants', 'tenants'), fromFeature('scim', 'scim')],
  },
  {
    key: 'governance',
    items: [
      fromFeature('groups', 'groups'),
      { key: 'toolApprovals', icon: 'rule' },
      { key: 'actOnBehalf', icon: 'badge' },
      { key: 'peoplesOwnApps', icon: 'apps' },
    ],
  },
  {
    key: 'security',
    items: [{ key: 'sso', icon: 'key' }, fromFeature('auditLogs', 'auditLogs')],
  },
  {
    key: 'cost',
    items: [fromFeature('usage', 'usage'), { key: 'indexingPriority', icon: 'low_priority' }],
  },
];
