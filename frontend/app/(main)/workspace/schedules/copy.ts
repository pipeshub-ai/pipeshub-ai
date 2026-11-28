import type { ErrorCodeOf } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';

/** What each schedule refusal and firing reason says (P8a's codes). */

/** Route refusals (`HTTP_AGENT_HARNESS_<code>`): every code of the catalogue's `schedule` group (ERR-UX). */
export const SCHEDULE_REFUSALS: Readonly<Record<ErrorCodeOf<'schedule'>, string>> = {
  SCHEDULED_RUNS_DISABLED: 'schedules.refusal.disabledForOrg',
  // No longer raised (ADR-SCH-02 D1); kept so an older server's refusal still reads in words.
  SCHEDULED_RUNS_UNAVAILABLE: 'schedules.refusal.unavailable',
  SCHEDULED_RUNS_STORE_UNAVAILABLE: 'schedules.refusal.storeUnavailable',
  SCHEDULE_INVALID: 'schedules.refusal.invalid',
  SCHEDULE_NOT_FOUND: 'schedules.refusal.notFound',
  SCHEDULE_FORBIDDEN: 'schedules.refusal.forbidden',
  SCHEDULE_DISABLED: 'schedules.refusal.scheduleDisabled',
  SCHEDULE_LIMIT: 'schedules.refusal.limit',
  SCHEDULE_BUDGET_EXCEEDS_CEILING: 'schedules.refusal.budgetCeiling',
  SCHEDULE_TRIGGER_TOKEN_INVALID: 'schedules.refusal.triggerTokenInvalid',
  SCHEDULE_RAN_ONCE: 'schedules.refusal.ranOnce',
  SCHEDULE_CHANGED: 'schedules.refusal.changed',
  SCHEDULE_NOT_APPROVED: 'schedules.refusal.notApproved',
  SCHEDULE_APPROVAL_USED: 'schedules.refusal.approvalUsed',
};

/** Why a firing was refused or skipped, and why the system paused or disabled a schedule. */
export const FIRING_REASONS: Readonly<Record<string, string>> = {
  scheduled_runs_disabled: 'schedules.reason.scheduledRunsDisabled',
  actor_deleted: 'schedules.reason.actorDeleted',
  actor_disabled: 'schedules.reason.actorDisabled',
  actor_not_person: 'schedules.reason.actorNotPerson',
  org_inactive: 'schedules.reason.orgInactive',
  agent_forbidden: 'schedules.reason.agentForbidden',
  agent_version_not_latest: 'schedules.reason.agentVersionNotLatest',
  agent_deleted: 'schedules.reason.agentDeleted',
  forbidden: 'schedules.reason.agentForbidden',
  narrowing: 'schedules.reason.narrowing',
  rollout_legacy: 'schedules.reason.rolloutLegacy',
  unavailable: 'schedules.reason.unavailable',
  template_missing: 'schedules.reason.templateMissing',
  catch_up_skip: 'schedules.reason.catchUpSkip',
  by_actor: 'schedules.reason.byActor',
  by_admin: 'schedules.reason.byAdmin',
};

export const firingReasonKey = (reason: string | null): string =>
  (reason !== null && FIRING_REASONS[reason]) || 'schedules.reason.other';

/** A one-off schedule that has fired (ADR-SCH-01 D1): kept with its run, never resumed or run again. */
export const RAN_ONCE = 'ran_once';

export const ranOnce = (schedule: { status: string; statusReason: string | null }): boolean =>
  schedule.status === 'disabled' && schedule.statusReason === RAN_ONCE;

/** A status the schedule's owner did not choose: the system paused or disabled it. A fired one-off is neither. */
export const isSystemStatus = (status: string, reason: string | null): boolean =>
  status !== 'active' && reason !== null && reason !== 'by_actor' && reason !== 'by_admin' && reason !== RAN_ONCE;
