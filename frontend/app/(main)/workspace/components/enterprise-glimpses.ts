import type { Glimpse } from './enterprise-glimpse';

/**
 * What each Enterprise Edition page looks like, as sample data for its blurred glimpse. Kept in
 * the shape of the real pages (columns, states, numbers) so the glimpse is recognisable.
 */
export const ENTERPRISE_GLIMPSES = {
  groups: {
    table: {
      columns: ['Group', 'Members', 'Can create', 'Synced from'],
      rows: [
        ['Engineering', '112', { pill: 'Agents, connectors', color: 'jade' }, 'Okta'],
        ['Finance', '24', { pill: 'Collections', color: 'blue' }, 'Okta'],
        ['Legal', '9', { pill: 'Collections', color: 'blue' }, 'Entra ID'],
        ['Support', '38', { pill: 'Agents', color: 'violet' }, 'Okta'],
        ['Everyone', '310', { pill: 'Nothing', color: 'gray' }, 'Workspace'],
      ],
    },
  },
  usage: {
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
  credits: {
    stats: [
      { label: 'Credits left', value: '42,500' },
      { label: 'Used this month', value: '18,240' },
      { label: 'People with credits', value: '310' },
    ],
    table: {
      columns: ['Person', 'Used', 'Limit', 'Left'],
      rows: [
        [{ person: 'Daniel Okafor' }, '1,820', '2,000', { pill: '9%', color: 'amber' }],
        [{ person: 'Sofia García' }, '940', '2,000', { pill: '53%', color: 'jade' }],
        [{ person: 'Liam Chen' }, '610', '1,000', { pill: '39%', color: 'jade' }],
        [{ person: 'Priya Sharma' }, '1,240', '2,000', { pill: '38%', color: 'jade' }],
      ],
    },
  },
  billing: {
    stats: [
      { label: 'Plan', value: 'Enterprise' },
      { label: 'Seats', value: '320 of 350' },
      { label: 'Next invoice', value: '$4,800.00' },
    ],
    table: {
      columns: ['Invoice', 'Date', 'Amount', 'Status'],
      rows: [
        ['INV-2026-011', '2026-11-01', '$4,800.00', { pill: 'Paid', color: 'jade' }],
        ['INV-2026-010', '2026-10-01', '$4,650.00', { pill: 'Paid', color: 'jade' }],
        ['INV-2026-009', '2026-09-01', '$4,650.00', { pill: 'Paid', color: 'jade' }],
      ],
    },
  },
  creditSettings: {
    settings: [
      { label: 'Alert admins at 80% of the monthly budget', on: true },
      { label: 'Alert a person at 90% of their credits', on: true },
      { label: 'Pause AI answers when credits run out', on: false },
      { label: 'Email a weekly usage summary', on: true },
    ],
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
  auditLogs: {
    table: {
      columns: ['Who', 'Action', 'Target', 'When', 'IP address'],
      rows: [
        [{ person: 'Priya Sharma' }, { pill: 'Connector added', color: 'jade' }, 'Google Drive', '2026-11-18 14:32', '10.4.2.17'],
        [{ person: 'Marco Rossi' }, { pill: 'Role changed', color: 'blue' }, 'Aiko Tanaka', '2026-11-18 11:05', '10.4.2.31'],
        [{ person: 'Aiko Tanaka' }, { pill: 'Agent shared', color: 'violet' }, 'Legal review', '2026-11-17 17:48', '10.4.3.8'],
        [{ person: 'Daniel Okafor' }, { pill: 'Record deleted', color: 'red' }, 'Q3 forecast.xlsx', '2026-11-17 09:12', '10.4.2.17'],
        [{ person: 'Sofia García' }, { pill: 'Signed in', color: 'gray' }, 'SAML', '2026-11-16 08:57', '10.4.5.2'],
      ],
    },
  },
} satisfies Record<string, Glimpse>;

export type EnterpriseGlimpseKey = keyof typeof ENTERPRISE_GLIMPSES;
