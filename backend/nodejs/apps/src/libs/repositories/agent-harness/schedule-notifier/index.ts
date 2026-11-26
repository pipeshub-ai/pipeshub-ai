import { pickHarnessStore } from '../stores';
import { mongoScheduleNoticeRecords } from './mongo';
import { pgScheduleNoticeRecords } from './pg';
import type { ScheduleNoticeRecords } from './records';

export type { ScheduleNoticeRecords };

/** Main's records a schedule notice reads and writes, on the selected document store. */
export const createScheduleNoticeRecords = (): ScheduleNoticeRecords =>
  pickHarnessStore<ScheduleNoticeRecords>('agent-harness/schedule-notifier', {
    mongo: () => mongoScheduleNoticeRecords,
    postgres: () => pgScheduleNoticeRecords,
  });
