/**
 * Convert an interval schedule into a cron expression. Ported from CLI
 * `src/sync/cron_from_schedule.ts` and kept in sync with the legacy frontend
 * version under `frontend/src/sections/accountdetails/connectors/utils/cron.ts`.
 */
export interface CronScheduleConfig {
  intervalMinutes?: number;
  timezone?: string;
}

export function buildCronFromSchedule(cfg?: CronScheduleConfig | null): string {
  const interval = Math.max(1, Number((cfg && cfg.intervalMinutes) || 60));

  const date = new Date();
  const minute = date.getUTCMinutes();
  const hour = date.getUTCHours();
  const dow = date.getUTCDay();

  if (interval < 60) return `*/${interval} * * * *`;
  if (interval % 60 === 0 && interval < 1440) {
    const hours = Math.max(1, Math.floor(interval / 60));
    return `${minute} */${hours} * * *`;
  }
  if (interval % 1440 === 0) {
    const days = Math.max(1, Math.floor(interval / 1440));
    if (days === 1) return `${minute} ${hour} * * *`;
    if (days % 7 === 0) return `${minute} ${hour} * * ${dow}`;
    return `${minute} ${hour} * * *`;
  }
  if (interval > 60) {
    const hours = Math.max(1, Math.floor(interval / 60));
    return `${minute} */${hours} * * *`;
  }
  return '0 * * * *';
}
