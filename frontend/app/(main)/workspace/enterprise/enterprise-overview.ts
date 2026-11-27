import { ENTERPRISE_FEATURES, type EnterpriseFeature } from './enterprise-feature-pages';

export interface EnterpriseOverviewItem {
  /** Names its copy: `workspace.enterprise.overview.items.<key>.{name,description}`. */
  key: string;
  icon: string;
  /** The locked page that shows it, when there is one. */
  feature?: EnterpriseFeature;
  /** Listed on the Enterprise plan card, among what matters most to an admin deciding. */
  highlight?: boolean;
  /**
   * The Community Edition covers part of the same need, said in
   * `workspace.enterprise.overview.compare.communityHas.<key>`; otherwise it has nothing for it.
   */
  inCommunity?: boolean;
}

export interface EnterpriseOverviewGroup {
  /** Names its heading: `workspace.enterprise.overview.groups.<key>`. */
  key: string;
  items: EnterpriseOverviewItem[];
}

const fromFeature = (
  key: string,
  feature: EnterpriseFeature,
  extra: Partial<EnterpriseOverviewItem> = {}
): EnterpriseOverviewItem => ({
  key,
  icon: ENTERPRISE_FEATURES[feature].icon,
  feature,
  ...extra,
});

/** Everything in the Enterprise Edition, grouped by what an admin is trying to do. */
export const ENTERPRISE_OVERVIEW_GROUPS: EnterpriseOverviewGroup[] = [
  {
    key: 'scale',
    items: [
      fromFeature('tenants', 'tenants', { highlight: true }),
      fromFeature('scim', 'scim', { highlight: true, inCommunity: true }),
      { key: 'collaborativeChat', icon: 'forum', inCommunity: true },
    ],
  },
  {
    key: 'governance',
    items: [
      fromFeature('groups', 'groups', { highlight: true, inCommunity: true }),
      { key: 'toolApprovals', icon: 'rule' },
      { key: 'actOnBehalf', icon: 'badge' },
      { key: 'peoplesOwnApps', icon: 'apps' },
    ],
  },
  {
    key: 'security',
    items: [
      { key: 'sso', icon: 'key', highlight: true, inCommunity: true },
      fromFeature('auditLogs', 'auditLogs', { highlight: true }),
    ],
  },
  {
    key: 'cost',
    items: [fromFeature('usage', 'usage', { highlight: true }), { key: 'indexingPriority', icon: 'low_priority' }],
  },
];

export const ENTERPRISE_OVERVIEW_ITEMS = ENTERPRISE_OVERVIEW_GROUPS.flatMap((group) => group.items);

/** What the Community Edition already has, on its plan card: `workspace.enterprise.overview.plans.community.items.<key>`. */
export const COMMUNITY_PLAN_ITEMS = ['search', 'agents', 'people', 'signIn'] as const;
