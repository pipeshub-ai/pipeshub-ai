/** Schedules and firings as P8a's routes answer them. */
export const scheduleView = (over: Record<string, unknown> = {}) => ({
  id: 'sch-1',
  name: 'Monday digest',
  actorId: 'u-ana',
  agentKey: 'agent-a',
  agentName: 'Support agent',
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
  maxCostUsd: 2,
  deadlineS: 28800,
  status: 'active',
  statusReason: null,
  nextFireAt: '2026-10-12T07:00:00.000Z',
  createdAt: '2026-10-06T10:00:00.000Z',
  updatedAt: '2026-10-06T10:00:00.000Z',
  input: { query: 'Summarise new tickets' },
  ...over,
});

export const firingView = (over: Record<string, unknown> = {}) => ({
  id: 'f1',
  kind: 'cron',
  slotAt: '2026-10-05T07:00:00.000Z',
  status: 'admitted',
  reason: null,
  runId: 'r1',
  conversationId: 'conv-1',
  missed: 0,
  attempts: 1,
  createdAt: '2026-10-05T07:00:01.000Z',
  settledAt: '2026-10-05T07:00:02.000Z',
  ...over,
});
