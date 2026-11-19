import type { Glimpse } from './enterprise-glimpse';

/**
 * What each Enterprise Edition page looks like, as sample data for its blurred glimpse. Kept in
 * the shape of the real pages (columns, states, numbers) so the glimpse is recognisable.
 */
export const ENTERPRISE_GLIMPSES = {
  // Shaped like the Enterprise Edition groups list: search and Create group above Name,
  // Description, Users and Teams, with grey tags only.
  groups: {
    toolbar: { search: 'Search groups or users', button: 'Create group', icon: 'group_add' },
    table: {
      columns: ['Name', 'Description', 'Users', 'Teams'],
      widths: ['1.2fr', '1.6fr', '1.1fr', '0.9fr'],
      rows: [
        [{ person: 'Admin' }, { muted: 'Manages settings, connectors and agents' }, { tags: ['Priya Sharma', '+4'] }, { tags: ['Platform'] }],
        [{ person: 'Everyone' }, { muted: 'Every member of the workspace' }, { tags: ['Marco Rossi', '+309'] }, { muted: '—' }],
        [{ person: 'Engineering' }, { muted: 'Creates agents and connectors' }, { tags: ['Aiko Tanaka', '+111'] }, { tags: ['Backend', 'Frontend', '+3'] }],
        [{ person: 'Finance' }, { muted: 'Creates and shares collections' }, { tags: ['Daniel Okafor', '+23'] }, { tags: ['FP&A'] }],
        [{ person: 'Support' }, { muted: 'Uses the support agents' }, { tags: ['Sofia García', '+37'] }, { tags: ['Tier 1', 'Tier 2'] }],
      ],
    },
  },
  // One page for usage, credits, billing and credit settings; the tabs show the four.
  usage: {
    tabs: { items: ['Usage', 'Credits', 'Billing', 'Credit settings'], buttons: ['This month', 'Export'] },
    stats: [
      { label: 'AI cost this month', value: '$1,284.20', delta: '12% less than last month' },
      { label: 'Questions answered', value: '18,402' },
      { label: 'People using AI', value: '312' },
    ],
    chart: [38, 52, 45, 70, 58, 66, 40, 84, 62, 48, 76, 60, 72, 55],
    table: {
      columns: ['Person', 'Agent', 'Model', 'Cost'],
      rows: [
        [{ person: 'Priya Sharma' }, 'Sales assistant', 'gpt-5', '$84.10'],
        [{ person: 'Marco Rossi' }, 'Support triage', 'claude-sonnet-5', '$61.75'],
        [{ person: 'Aiko Tanaka' }, 'Legal review', 'claude-opus-5', '$58.20'],
      ],
    },
  },
  scim: {
    field: { label: 'SCIM address', value: 'https://pipeshub.acme.io/api/v1/scim/v2' },
    table: {
      columns: ['Token', 'Made by', 'Made on', 'Ends on', 'State'],
      rows: [
        ['•••• 4f2a', { person: 'Priya Sharma' }, '2026-10-02', '2027-10-02', { pill: 'Active', color: 'jade' }],
        ['•••• 91c7', { person: 'Marco Rossi' }, '2025-12-14', '2026-12-14', { pill: 'Ends soon', color: 'amber' }],
      ],
    },
  },
  // Shaped like the Enterprise Edition audit logs screen: Events tab, filter bar, then one row
  // per event with its action key, the actor's email and an outcome badge.
  auditLogs: {
    tabs: { items: ['Events', 'Charts', 'Alerts', 'Settings'], buttons: ['Acme Corp', 'Saved searches', 'Export'] },
    filters: {
      chips: ['Last 24 hours', 'Actions', 'Actor', 'Entity type', 'Outcome'],
      fields: ['Entity ID', 'IP address', 'Request ID'],
    },
    table: {
      columns: ['Time', 'Action', 'Actor', 'Entity', 'Outcome', 'IP address'],
      widths: ['1.3fr', '1.4fr', '1.3fr', '1.1fr', '0.7fr', '0.8fr'],
      rows: [
        [
          'Nov 18, 2026, 02:32:07 PM',
          { primary: 'Connector created', secondary: 'connector_created' },
          { primary: 'Priya Sharma', secondary: 'priya@acme.io' },
          { primary: 'Google Drive', secondary: 'connector' },
          { pill: 'Success', color: 'jade' },
          '10.4.2.17',
        ],
        [
          'Nov 18, 2026, 11:05:44 AM',
          { primary: 'User role changed', secondary: 'user_role_changed' },
          { primary: 'Marco Rossi', secondary: 'marco@acme.io' },
          { primary: 'Aiko Tanaka', secondary: 'user' },
          { pill: 'Success', color: 'jade' },
          '10.4.2.31',
        ],
        [
          'Nov 17, 2026, 05:48:12 PM',
          { primary: 'Permission denied', secondary: 'permission_denied' },
          { primary: 'Liam Chen', secondary: 'liam@acme.io' },
          { primary: 'Billing settings', secondary: 'org' },
          { pill: 'Denied', color: 'amber' },
          '10.4.3.8',
        ],
        [
          'Nov 17, 2026, 09:12:55 AM',
          { primary: 'Agent tool called', secondary: 'agent_tool_called' },
          { primary: 'Daniel Okafor', secondary: 'daniel@acme.io' },
          { primary: 'Legal review', secondary: 'agent' },
          { pill: 'Success', color: 'jade' },
          '10.4.2.17',
        ],
        [
          'Nov 16, 2026, 08:57:30 AM',
          { primary: 'User login failed', secondary: 'user_login_failed' },
          { primary: 'Sofia García', secondary: 'sofia@acme.io' },
          { primary: 'SAML', secondary: 'auth' },
          { pill: 'Failure', color: 'red' },
          '10.4.5.2',
        ],
      ],
    },
  },
} satisfies Record<string, Glimpse>;

export type EnterpriseGlimpseKey = keyof typeof ENTERPRISE_GLIMPSES;
