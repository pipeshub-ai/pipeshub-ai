import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildCrawlingScheduleFromSync,
  isScheduledSyncStrategy,
  SCHEDULED_STRATEGY,
  ConnectorSyncBlock,
} from '../../../../src/modules/crawling_manager/utils/schedule_config_mapper';
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums';

describe('schedule_config_mapper', () => {
  // ------------------------------------------------------------
  // isScheduledSyncStrategy
  // ------------------------------------------------------------
  describe('isScheduledSyncStrategy', () => {
    it('returns true for selectedStrategy = "SCHEDULED"', () => {
      expect(isScheduledSyncStrategy({ selectedStrategy: 'SCHEDULED' })).to.equal(true);
    });

    it('returns true for lowercase "scheduled" (case-insensitive)', () => {
      expect(isScheduledSyncStrategy({ selectedStrategy: 'scheduled' })).to.equal(true);
    });

    it('returns true for mixed case "Scheduled"', () => {
      expect(isScheduledSyncStrategy({ selectedStrategy: 'Scheduled' })).to.equal(true);
    });

    it('returns false for MANUAL', () => {
      expect(isScheduledSyncStrategy({ selectedStrategy: 'MANUAL' })).to.equal(false);
    });

    it('returns false for WEBHOOK', () => {
      expect(isScheduledSyncStrategy({ selectedStrategy: 'WEBHOOK' })).to.equal(false);
    });

    it('returns false for null sync', () => {
      expect(isScheduledSyncStrategy(null)).to.equal(false);
    });

    it('returns false for undefined sync', () => {
      expect(isScheduledSyncStrategy(undefined)).to.equal(false);
    });

    it('returns false when selectedStrategy is missing', () => {
      expect(isScheduledSyncStrategy({} as ConnectorSyncBlock)).to.equal(false);
    });

    it('returns false when selectedStrategy is not a string', () => {
      expect(
        isScheduledSyncStrategy({ selectedStrategy: 123 as unknown as string }),
      ).to.equal(false);
    });

    it('SCHEDULED_STRATEGY constant equals the literal "SCHEDULED"', () => {
      expect(SCHEDULED_STRATEGY).to.equal('SCHEDULED');
    });
  });

  // ------------------------------------------------------------
  // buildCrawlingScheduleFromSync
  // ------------------------------------------------------------
  describe('buildCrawlingScheduleFromSync', () => {
    const validUserId = '69cf5a063f9154c55dcf81cc'; // 24-char hex (valid ObjectId)

    it('builds INTERVAL schedule for SCHEDULED strategy with valid intervalMinutes', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 30, timezone: 'UTC' },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);

      expect(result).to.not.equal(null);
      expect(result!.scheduleType).to.equal(CrawlingScheduleType.INTERVAL);
      expect(result!.isEnabled).to.equal(true);
      expect((result! as any).scheduleConfig.intervalMinutes).to.equal(30);
      expect((result! as any).scheduleConfig.timezone).to.equal('UTC');
    });

    it('defaults timezone to UTC when missing', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 60 },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);
      expect((result! as any).scheduleConfig.timezone).to.equal('UTC');
    });

    it('defaults timezone to UTC when empty string', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 15, timezone: '   ' },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);
      expect((result! as any).scheduleConfig.timezone).to.equal('UTC');
    });

    it('preserves a non-UTC timezone', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 5, timezone: 'America/Los_Angeles' },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);
      expect((result! as any).scheduleConfig.timezone).to.equal('America/Los_Angeles');
    });

    it('floors fractional intervalMinutes', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 30.7 as number },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);
      expect((result! as any).scheduleConfig.intervalMinutes).to.equal(30);
    });

    it('handles non-divisor-of-60 intervals (e.g. 45)', () => {
      // The whole point of INTERVAL: cron `*/45 * * * *` is broken;
      // INTERVAL uses BullMQ `every` so 45 minutes is exactly 45 minutes.
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 45 },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);
      expect(result).to.not.equal(null);
      expect((result! as any).scheduleConfig.intervalMinutes).to.equal(45);
    });

    it('returns null for MANUAL strategy', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'MANUAL',
        scheduledConfig: { intervalMinutes: 30 },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null for WEBHOOK strategy', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'WEBHOOK',
        scheduledConfig: { intervalMinutes: 30 },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null when sync is null', () => {
      expect(buildCrawlingScheduleFromSync(null, validUserId)).to.equal(null);
    });

    it('returns null when sync is undefined', () => {
      expect(buildCrawlingScheduleFromSync(undefined, validUserId)).to.equal(null);
    });

    it('returns null when scheduledConfig is missing', () => {
      const sync: ConnectorSyncBlock = { selectedStrategy: 'SCHEDULED' };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null when intervalMinutes is missing', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { timezone: 'UTC' },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null when intervalMinutes is 0', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 0 },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null when intervalMinutes is negative', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: -10 },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null when intervalMinutes is NaN', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: Number.NaN },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('returns null when intervalMinutes is non-numeric string', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 'thirty' as unknown as number },
      };
      expect(buildCrawlingScheduleFromSync(sync, validUserId)).to.equal(null);
    });

    it('coerces numeric-string intervalMinutes (e.g. "30")', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: '30' as unknown as number },
      };
      const result = buildCrawlingScheduleFromSync(sync, validUserId);
      expect(result).to.not.equal(null);
      expect((result! as any).scheduleConfig.intervalMinutes).to.equal(30);
    });

    it('accepts a non-ObjectId userId without throwing (e.g. "system")', () => {
      const sync: ConnectorSyncBlock = {
        selectedStrategy: 'SCHEDULED',
        scheduledConfig: { intervalMinutes: 30 },
      };
      const result = buildCrawlingScheduleFromSync(sync, 'system');
      expect(result).to.not.equal(null);
    });
  });
});
