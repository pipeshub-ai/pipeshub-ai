import { expect } from "chai";
import { buildCronFromSchedule } from "../src/sync/cron_from_schedule";

/** Fixed UTC instant: 2024-01-01 12:30:00 → minute 30, hour 12, Monday (dow 1). */
const FIXED_START = Date.UTC(2024, 0, 1, 12, 30, 0);

describe("cron_from_schedule", () => {
  it("clamps negative interval to 1 for sub-hourly", () => {
    const c = buildCronFromSchedule({
      intervalMinutes: -5,
      startTime: FIXED_START,
    });
    expect(c).to.equal("*/1 * * * *");
  });

  it("sub-hourly every N minutes", () => {
    expect(
      buildCronFromSchedule({ intervalMinutes: 15, startTime: FIXED_START })
    ).to.equal("*/15 * * * *");
  });

  it("hourly multiple uses minute from startTime", () => {
    expect(
      buildCronFromSchedule({ intervalMinutes: 120, startTime: FIXED_START })
    ).to.equal("30 */2 * * *");
  });

  it("daily when interval is 1440 minutes", () => {
    expect(
      buildCronFromSchedule({ intervalMinutes: 1440, startTime: FIXED_START })
    ).to.equal("30 12 * * *");
  });

  it("weekly when interval is multiple of 7 days", () => {
    expect(
      buildCronFromSchedule({ intervalMinutes: 10080, startTime: FIXED_START })
    ).to.equal("30 12 * * 1");
  });

  it("multi-day non-weekly uses daily-style pattern", () => {
    expect(
      buildCronFromSchedule({ intervalMinutes: 2880, startTime: FIXED_START })
    ).to.equal("30 12 * * *");
  });

  it("defaults interval to 60 when omitted", () => {
    const c = buildCronFromSchedule({ startTime: FIXED_START });
    expect(c).to.equal("30 */1 * * *");
  });

  it("odd interval over 60 uses hour floor branch", () => {
    expect(
      buildCronFromSchedule({ intervalMinutes: 90, startTime: FIXED_START })
    ).to.equal("30 */1 * * *");
  });
});
