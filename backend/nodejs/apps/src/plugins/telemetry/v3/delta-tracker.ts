/**
 * Turns the registry's ever-growing counters into what v3 sends: the amount
 * counted since the last batch was cut.
 *
 * `cut` moves the baseline, so the caller must keep the batch it got until
 * the collector acknowledges it; what is counted meanwhile stays in the
 * registry and is in the next cut. `rebase` moves the baseline without
 * producing a batch, for counts that reached the collector another way (the
 * legacy endpoints send the cumulative values themselves).
 */
import { seriesKey, V3Series } from './translate';

export class DeltaTracker {
  private baseline = new Map<string, number>();

  /** Counters as deltas since the last cut or rebase (only those that moved); gauges as they are. */
  cut(current: V3Series[]): V3Series[] {
    const out: V3Series[] = [];
    const next = new Map<string, number>();
    for (const series of current) {
      if (series.type !== 'counter') {
        out.push(series);
        continue;
      }
      const key = seriesKey(series);
      const before = this.baseline.get(key) ?? 0;
      // Below the baseline: the registry was reset, so it counts from zero again.
      const delta =
        series.value >= before ? series.value - before : series.value;
      next.set(key, series.value);
      if (delta > 0) out.push({ ...series, value: delta });
    }
    this.baseline = next;
    return out;
  }

  rebase(current: V3Series[]): void {
    const next = new Map<string, number>();
    for (const series of current) {
      if (series.type === 'counter') next.set(seriesKey(series), series.value);
    }
    this.baseline = next;
  }
}
