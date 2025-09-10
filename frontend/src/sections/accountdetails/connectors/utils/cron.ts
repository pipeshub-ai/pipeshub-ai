export interface ScheduledConfigLike {
  startTime?: number; // epoch ms
  intervalMinutes?: number; // minutes
  timezone?: string;
}

export function buildCronFromSchedule(cfg: ScheduledConfigLike): string {
  const interval = Math.max(1, Number(cfg.intervalMinutes || 60));
  const startMs = Number(cfg.startTime || 0);

  const date = startMs > 0 ? new Date(startMs) : new Date();
  const minute = date.getUTCMinutes();
  const hour = date.getUTCHours();
  const dow = date.getUTCDay(); // 0-6 Sun-Sat

  if (interval < 60) {
    return `*/${interval} * * * *`;
  }

  if (interval % 60 === 0 && interval < 1440) {
    const hours = Math.max(1, Math.floor(interval / 60));
    return `${minute} */${hours} * * *`;
  }

  if (interval % 1440 === 0) {
    const days = Math.max(1, Math.floor(interval / 1440));
    if (days === 1) {
      return `${minute} ${hour} * * *`;
    }
    if (days % 7 === 0) {
      return `${minute} ${hour} * * ${dow}`;
    }
    return `${minute} ${hour} * * *`;
  }

  if (interval > 60) {
    const hours = Math.max(1, Math.floor(interval / 60));
    return `${minute} */${hours} * * *`;
  }

  return '0 * * * *';
}


