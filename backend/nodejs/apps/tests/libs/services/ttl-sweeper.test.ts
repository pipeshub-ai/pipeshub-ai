import { expect } from 'chai';
import {
  activityRetentionDays,
  TtlSweeper,
} from '../../../src/libs/services/ttl-sweeper.service';

describe('USER_ACTIVITY_RETENTION_DAYS', () => {
  // the default is read from the environment, so a developer's own setting must not reach the test
  let saved: string | undefined;
  beforeEach(() => {
    saved = process.env.USER_ACTIVITY_RETENTION_DAYS;
    delete process.env.USER_ACTIVITY_RETENTION_DAYS;
  });
  afterEach(() => {
    if (saved === undefined) delete process.env.USER_ACTIVITY_RETENTION_DAYS;
    else process.env.USER_ACTIVITY_RETENTION_DAYS = saved;
  });

  it('defaults to 90 days and takes a whole number of days', () => {
    expect(activityRetentionDays()).to.equal(90);
    expect(activityRetentionDays(undefined)).to.equal(90);
    expect(activityRetentionDays(' ')).to.equal(90);
    expect(activityRetentionDays('30')).to.equal(30);
  });

  it('refuses anything else, naming the setting', () => {
    for (const bad of ['0', '-1', '1.5', 'ninety', '7d'])
      expect(() => activityRetentionDays(bad)).to.throw(
        /USER_ACTIVITY_RETENTION_DAYS/,
      );
  });
});

describe('TtlSweeper without the jobs-role tables (MongoDB)', () => {
  it('does not run the PostgreSQL activity trim', async () => {
    const errors: unknown[] = [];
    const logger = {
      info() {},
      warn() {},
      error: (...a: unknown[]) => errors.push(a),
    } as never;
    const sweeper = new TtlSweeper(
      logger,
      60_000,
      () => new Date(),
      90,
      undefined,
      false,
    );
    await sweeper.sweepOnce();
    expect(errors).to.deep.equal([]);
  });
});
