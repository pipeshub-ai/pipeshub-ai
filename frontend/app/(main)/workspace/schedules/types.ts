/** Scheduled and triggered agent runs (P8a) as the schedules page sees them. */

export type CatchUpPolicy = 'skip' | 'run_latest' | 'run_all';
/** What a firing does while the schedule's last run is still going (TR-06). */
export type OverlapPolicy = 'skip' | 'queue' | 'allow';
/** Where the schedule's owner is told a run ended (TR-07). */
export type NotifyChannel = 'in_app' | 'email' | 'slack';
/** How a webhook caller proves it holds the secret (TR-04). */
export type WebhookAuth = 'signature' | 'token';
export type ScheduleStatus = 'active' | 'paused' | 'disabled';
export type FiringKind = 'cron' | 'manual' | 'event';
export type FiringStatus = 'pending' | 'admitted' | 'refused' | 'skipped';
/** A published agent version (from 1), or `latest` to follow every publish (OWD-14). */
export type AgentVersionPin = number | 'latest';

export type ScheduleTrigger =
  | { kind: 'cron'; cron: string; timeZone: string; catchUp: CatchUpPolicy }
  | { kind: 'webhook'; coalesceS: number; auth: WebhookAuth }
  | { kind: 'connector'; connectorId: string; coalesceS: number };

export interface Schedule {
  id: string;
  name: string;
  /** Who every run acts as: the creator. */
  actorId: string;
  /** Null: the built-in assistant. */
  agentKey: string | null;
  /** The agent version its firings run; null for the assistant. */
  agentVersion: AgentVersionPin | null;
  /** The agent's name, as the list answers it (ADR-UI-03); null: the assistant, or nobody could name it now. */
  agentName: string | null;
  /** The agent's latest published version, as the list answers it (ADR-UI-03); null: unknown. */
  agentLiveVersion: number | null;
  trigger: ScheduleTrigger;
  maxCostUsd: number | null;
  deadlineS: number;
  status: ScheduleStatus;
  /** A reason code, or `by_actor` / `by_admin`. */
  statusReason: string | null;
  nextFireAt: string | null;
  createdAt: string;
  /** The prompt; the server sends it to the schedule's own actor only. */
  query: string | null;
  overlap: OverlapPolicy;
  notify: NotifyChannel[];
}

export interface Firing {
  id: string;
  kind: FiringKind;
  slotAt: string;
  status: FiringStatus;
  reason: string | null;
  runId: string | null;
  conversationId: string | null;
  missed: number;
  attempts: number;
  /** When the firing was made; a cron firing made well after its slot was caught up after an outage. */
  createdAt: string | null;
}

export type NewTrigger =
  | { kind: 'cron'; cron: string; timeZone: string; catchUp: CatchUpPolicy }
  | { kind: 'webhook'; auth: WebhookAuth };

export interface NewSchedule {
  name: string;
  agentKey: string | null;
  /** Absent: the version live now, pinned. Only with an agent. */
  agentVersion?: 'latest';
  query: string;
  trigger: NewTrigger;
  maxCostUsd: number | null;
  deadlineS: number | null;
  overlap: OverlapPolicy;
  notify: NotifyChannel[];
}

export interface CreatedSchedule {
  schedule: Schedule;
  /** An `auth: token` webhook's token: answered once, never stored by the page. */
  webhookToken: string | null;
  /** A signed webhook's signing key (TR-04): answered once, never stored by the page. */
  webhookSecret: string | null;
}

/** Why a notice channel cannot be picked (TR-07). */
export type NoticeChannelBlock = 'org_off' | 'no_slack_bot';

export interface NoticeChannelChoice {
  channel: NotifyChannel;
  available: boolean;
  reason: NoticeChannelBlock | null;
}

/** Whose schedules a list holds: the person's own. */
export type ScheduleScope = 'mine';
