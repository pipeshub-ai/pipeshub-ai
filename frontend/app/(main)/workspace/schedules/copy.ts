import type { ErrorCodeOf } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';

/** What each schedule refusal and firing reason says (P8a's codes). */

/** Route refusals (`HTTP_AGENT_HARNESS_<code>`): every code of the catalogue's `schedule` group (ERR-UX). */
export const SCHEDULE_REFUSALS: Readonly<Record<ErrorCodeOf<'schedule'>, string>> = {
  SCHEDULED_RUNS_DISABLED: 'schedules.refusal.disabledForOrg',
  SCHEDULED_RUNS_UNAVAILABLE: 'schedules.refusal.unavailable',
  SCHEDULE_INVALID: 'schedules.refusal.invalid',
  SCHEDULE_NOT_FOUND: 'schedules.refusal.notFound',
  SCHEDULE_FORBIDDEN: 'schedules.refusal.forbidden',
  SCHEDULE_DISABLED: 'schedules.refusal.scheduleDisabled',
  SCHEDULE_LIMIT: 'schedules.refusal.limit',
  SCHEDULE_BUDGET_EXCEEDS_CEILING: 'schedules.refusal.budgetCeiling',
  SCHEDULE_TRIGGER_TOKEN_INVALID: 'schedules.refusal.triggerTokenInvalid',
};

/** Why a firing was refused or skipped, and why the system paused or disabled a schedule. */
export const FIRING_REASONS: Readonly<Record<string, string>> = {
  scheduled_runs_disabled: 'schedules.reason.scheduledRunsDisabled',
  actor_deleted: 'schedules.reason.actorDeleted',
  actor_disabled: 'schedules.reason.actorDisabled',
  actor_not_person: 'schedules.reason.actorNotPerson',
  org_inactive: 'schedules.reason.orgInactive',
  agent_forbidden: 'schedules.reason.agentForbidden',
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

/** A status the schedule's owner did not choose: the system paused or disabled it. */
export const isSystemStatus = (status: string, reason: string | null): boolean =>
  status !== 'active' && reason !== null && reason !== 'by_actor' && reason !== 'by_admin';
