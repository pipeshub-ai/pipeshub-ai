/**
 * Utility functions for time calculations and conversions
 */

export interface TimeZoneInfo {
  name: string;
  offset: string;
  displayName: string;
}

export const TIMEZONES: TimeZoneInfo[] = [
  { name: 'UTC', offset: '+00:00', displayName: 'UTC (Coordinated Universal Time)' },
  { name: 'America/New_York', offset: '-05:00', displayName: 'Eastern Time (ET)' },
  { name: 'America/Chicago', offset: '-06:00', displayName: 'Central Time (CT)' },
  { name: 'America/Denver', offset: '-07:00', displayName: 'Mountain Time (MT)' },
  { name: 'America/Los_Angeles', offset: '-08:00', displayName: 'Pacific Time (PT)' },
  { name: 'Europe/London', offset: '+00:00', displayName: 'Greenwich Mean Time (GMT)' },
  { name: 'Europe/Paris', offset: '+01:00', displayName: 'Central European Time (CET)' },
  { name: 'Europe/Berlin', offset: '+01:00', displayName: 'Central European Time (CET)' },
  { name: 'Europe/Rome', offset: '+01:00', displayName: 'Central European Time (CET)' },
  { name: 'Europe/Madrid', offset: '+01:00', displayName: 'Central European Time (CET)' },
  { name: 'Asia/Tokyo', offset: '+09:00', displayName: 'Japan Standard Time (JST)' },
  { name: 'Asia/Shanghai', offset: '+08:00', displayName: 'China Standard Time (CST)' },
  { name: 'Asia/Hong_Kong', offset: '+08:00', displayName: 'Hong Kong Time (HKT)' },
  { name: 'Asia/Singapore', offset: '+08:00', displayName: 'Singapore Standard Time (SGT)' },
  { name: 'Asia/Kolkata', offset: '+05:30', displayName: 'India Standard Time (IST)' },
  { name: 'Asia/Dubai', offset: '+04:00', displayName: 'Gulf Standard Time (GST)' },
  { name: 'Australia/Sydney', offset: '+10:00', displayName: 'Australian Eastern Time (AET)' },
  { name: 'Australia/Melbourne', offset: '+10:00', displayName: 'Australian Eastern Time (AET)' },
  { name: 'Pacific/Auckland', offset: '+12:00', displayName: 'New Zealand Time (NZST)' },
];

export const INTERVAL_OPTIONS = [
  { value: 5, label: '5 minutes', description: 'Very frequent updates' },
  { value: 15, label: '15 minutes', description: 'Frequent updates' },
  { value: 30, label: '30 minutes', description: 'Regular updates' },
  { value: 60, label: '1 hour', description: 'Hourly updates' },
  { value: 120, label: '2 hours', description: 'Every 2 hours' },
  { value: 240, label: '4 hours', description: 'Every 4 hours' },
  { value: 480, label: '8 hours', description: 'Every 8 hours' },
  { value: 720, label: '12 hours', description: 'Twice daily' },
  { value: 1440, label: '1 day', description: 'Daily updates' },
  { value: 2880, label: '2 days', description: 'Every other day' },
  { value: 10080, label: '1 week', description: 'Weekly updates' },
];

/**
 * Convert a Date object to epoch seconds
 */
export const dateToEpochSeconds = (date: Date): number => Math.floor(date.getTime() / 1000);

/**
 * Convert epoch seconds to a Date object
 */
export const epochSecondsToDate = (epoch: number): Date => new Date(epoch * 1000);

/**
 * Format epoch time to a readable string with timezone support
 */
export const formatEpochTime = (epoch: number, timezone?: string): string => {
  if (epoch === 0) return 'Never';

  const date = epochSecondsToDate(epoch);

  try {
    if (timezone && timezone !== 'UTC') {
      return date.toLocaleString('en-US', {
        timeZone: timezone,
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        timeZoneName: 'short',
      });
    }

    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
    });
  } catch (error) {
    // Fallback to UTC if timezone is invalid
    return date.toLocaleString('en-US', {
      timeZone: 'UTC',
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
    });
  }
};

/**
 * Calculate next execution time based on interval and current time
 */
export const calculateNextExecution = (
  startTime: number,
  intervalMinutes: number,
  currentTime?: number
): number => {
  const now = currentTime || Math.floor(Date.now() / 1000);
  const intervalSeconds = intervalMinutes * 60;

  if (now < startTime) {
    return startTime;
  }

  const elapsed = now - startTime;
  const intervalsPassed = Math.floor(elapsed / intervalSeconds);

  return startTime + (intervalsPassed + 1) * intervalSeconds;
};

/**
 * Calculate end time based on start time, interval, and repetitions
 */
export const calculateEndTime = (
  startTime: number,
  intervalMinutes: number,
  maxRepetitions: number
): number => {
  if (maxRepetitions === 0) {
    return 0; // Infinite repetitions
  }

  const intervalSeconds = intervalMinutes * 60;
  return startTime + maxRepetitions * intervalSeconds;
};

/**
 * Validate scheduled sync configuration
 */
export interface ScheduledSyncValidation {
  isValid: boolean;
  errors: string[];
  warnings: string[];
}

export const validateScheduledSync = (config: {
  enabled: boolean;
  startDateTime: Date | null;
  intervalMinutes: number;
  maxRepetitions: number;
  isRecurring: boolean;
}): ScheduledSyncValidation => {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (!config.enabled) {
    return { isValid: true, errors: [], warnings: [] };
  }

  // Required field validation
  if (!config.startDateTime) {
    errors.push('Start date and time is required');
  }

  if (config.intervalMinutes <= 0) {
    errors.push('Interval must be greater than 0 minutes');
  }

  // Minimum interval validation
  if (config.intervalMinutes < 5) {
    errors.push('Minimum sync interval is 5 minutes to prevent system overload');
  }

  // Recurring sync validation
  if (config.isRecurring && config.maxRepetitions < 0) {
    errors.push('Max repetitions cannot be negative');
  }

  // One-time sync validation
  if (!config.isRecurring && config.maxRepetitions !== 1) {
    warnings.push('One-time sync will be automatically set to 1 repetition');
  }

  // Time validation
  if (config.startDateTime) {
    const now = new Date();
    const startTime = new Date(config.startDateTime);

    // Warning for past start time on one-time sync
    if (!config.isRecurring && startTime < now) {
      errors.push('Start time cannot be in the past for one-time sync');
    }

    // Warning for past start time on recurring sync
    if (config.isRecurring && startTime < now) {
      warnings.push('Start time is in the past. Next execution will be calculated automatically.');
    }

    // Warning for very frequent intervals
    if (config.intervalMinutes < 15) {
      warnings.push('Very frequent sync intervals may impact system performance');
    }

    // Warning for very high repetition count
    if (config.isRecurring && config.maxRepetitions > 1000) {
      warnings.push('High repetition count may run for a very long time');
    }
  }

  return {
    isValid: errors.length === 0,
    errors,
    warnings,
  };
};

/**
 * Get timezone display name
 */
export const getTimezoneDisplayName = (timezone: string): string => {
  const tzInfo = TIMEZONES.find((tz) => tz.name === timezone);
  return tzInfo ? tzInfo.displayName : timezone;
};

/**
 * Get interval display name
 */
export const getIntervalDisplayName = (intervalMinutes: number): string => {
  const interval = INTERVAL_OPTIONS.find((opt) => opt.value === intervalMinutes);
  return interval ? interval.label : `${intervalMinutes} minutes`;
};

/**
 * Calculate duration between two epoch times
 */
export const calculateDuration = (startEpoch: number, endEpoch: number): string => {
  if (endEpoch === 0) return 'Infinite';

  const diffSeconds = endEpoch - startEpoch;
  const diffMinutes = Math.floor(diffSeconds / 60);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffDays > 0) {
    const remainingHours = diffHours % 24;
    const remainingMinutes = diffMinutes % 60;

    let duration = `${diffDays}d`;
    if (remainingHours > 0) duration += ` ${remainingHours}h`;
    if (remainingMinutes > 0 && diffDays < 7) duration += ` ${remainingMinutes}m`;

    return duration;
  }
  if (diffHours > 0) {
    const remainingMinutes = diffMinutes % 60;
    let duration = `${diffHours}h`;
    if (remainingMinutes > 0) duration += ` ${remainingMinutes}m`;
    return duration;
  }
  return `${diffMinutes}m`;
};

/**
 * Get relative time description (e.g., "in 2 hours", "5 minutes ago")
 */
export const getRelativeTime = (epochTime: number, currentTime?: number): string => {
  const now = currentTime || Math.floor(Date.now() / 1000);
  const diff = epochTime - now;
  const absDiff = Math.abs(diff);

  if (absDiff < 60) {
    return diff > 0 ? 'very soon' : 'just now';
  }

  const minutes = Math.floor(absDiff / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  let timeStr = '';
  if (days > 0) {
    timeStr = `${days} day${days > 1 ? 's' : ''}`;
  } else if (hours > 0) {
    timeStr = `${hours} hour${hours > 1 ? 's' : ''}`;
  } else {
    timeStr = `${minutes} minute${minutes > 1 ? 's' : ''}`;
  }

  return diff > 0 ? `in ${timeStr}` : `${timeStr} ago`;
};

/**
 * Check if a given epoch time is in the future
 */
export const isFuture = (epochTime: number, currentTime?: number): boolean => {
  const now = currentTime || Math.floor(Date.now() / 1000);
  return epochTime > now;
};

/**
 * Check if a given epoch time is in the past
 */
export const isPast = (epochTime: number, currentTime?: number): boolean => {
  const now = currentTime || Math.floor(Date.now() / 1000);
  return epochTime < now;
};
