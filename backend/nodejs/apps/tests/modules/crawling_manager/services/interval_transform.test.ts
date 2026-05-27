import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service';
import { CrawlingScheduleType } from '../../../../src/modules/crawling_manager/schema/enums';

/**
 * `transformScheduleConfig` is private; we exercise it via prototype access
 * on a stub `this` so we don't need to wire BullMQ / Redis for the unit test.
 */
const callTransform = (scheduleConfig: any): any => {
  const fn = (CrawlingSchedulerService.prototype as any).transformScheduleConfig;
  const ctx = {
    logger: {
      info: sinon.stub(),
      debug: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    },
  };
  return fn.call(ctx, scheduleConfig);
};

describe('CrawlingSchedulerService.transformScheduleConfig', () => {
  // ------------------------------------------------------------
  // INTERVAL
  // ------------------------------------------------------------
  describe('INTERVAL', () => {
    it('returns { every: ms, tz } for valid intervalMinutes', () => {
      const opts = callTransform({
        scheduleType: CrawlingScheduleType.INTERVAL,
        scheduleConfig: { intervalMinutes: 30, timezone: 'UTC' },
      });
      expect(opts).to.deep.equal({ every: 30 * 60 * 1000, tz: 'UTC' });
    });

    it('handles non-divisor-of-60 intervals (45 min) precisely', () => {
      const opts = callTransform({
        scheduleType: CrawlingScheduleType.INTERVAL,
        scheduleConfig: { intervalMinutes: 45, timezone: 'UTC' },
      });
      expect(opts.every).to.equal(45 * 60 * 1000);
    });

    it('handles long intervals (24 hours = 1440 min)', () => {
      const opts = callTransform({
        scheduleType: CrawlingScheduleType.INTERVAL,
        scheduleConfig: { intervalMinutes: 1440 },
      });
      expect(opts.every).to.equal(1440 * 60 * 1000);
      expect(opts.tz).to.equal('UTC'); // default from top-level destructure
    });

    it('floors fractional intervalMinutes', () => {
      const opts = callTransform({
        scheduleType: CrawlingScheduleType.INTERVAL,
        scheduleConfig: { intervalMinutes: 30.9 },
      });
      expect(opts.every).to.equal(30 * 60 * 1000);
    });

    it('throws BadRequestError for intervalMinutes = 0', () => {
      expect(() =>
        callTransform({
          scheduleType: CrawlingScheduleType.INTERVAL,
          scheduleConfig: { intervalMinutes: 0 },
        }),
      ).to.throw(/positive integer/);
    });

    it('throws for negative intervalMinutes', () => {
      expect(() =>
        callTransform({
          scheduleType: CrawlingScheduleType.INTERVAL,
          scheduleConfig: { intervalMinutes: -5 },
        }),
      ).to.throw(/positive integer/);
    });

    it('throws for NaN intervalMinutes', () => {
      expect(() =>
        callTransform({
          scheduleType: CrawlingScheduleType.INTERVAL,
          scheduleConfig: { intervalMinutes: Number.NaN },
        }),
      ).to.throw(/positive integer/);
    });
  });

  // ------------------------------------------------------------
  // CUSTOM (regression: existing path must keep working)
  // ------------------------------------------------------------
  describe('CUSTOM (regression)', () => {
    it('returns { pattern, tz } for cron expression', () => {
      const opts = callTransform({
        scheduleType: CrawlingScheduleType.CUSTOM,
        cronExpression: '0 */2 * * *',
        timezone: 'America/New_York',
      });
      expect(opts).to.deep.equal({
        pattern: '0 */2 * * *',
        tz: 'America/New_York',
      });
    });
  });

  // ------------------------------------------------------------
  // ONCE (regression)
  // ------------------------------------------------------------
  describe('ONCE (regression)', () => {
    it('returns undefined (one-shot delay handled by jobOptions)', () => {
      const opts = callTransform({
        scheduleType: CrawlingScheduleType.ONCE,
        scheduledTime: new Date().toISOString(),
      });
      expect(opts).to.equal(undefined);
    });
  });

  // ------------------------------------------------------------
  // Invalid scheduleType
  // ------------------------------------------------------------
  describe('Invalid', () => {
    it('throws on unknown scheduleType', () => {
      expect(() =>
        callTransform({ scheduleType: 'mystery' as any }),
      ).to.throw(/Invalid schedule type/);
    });
  });
});
