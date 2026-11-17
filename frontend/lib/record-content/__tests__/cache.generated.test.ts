// @vitest-environment node
/**
 * The product cache against the reference model (see reference-model.ts): randomised worlds with
 * a server of its own, then every control, which must fail the invariant it names.
 *
 * Fixed seeds by default. `RECORD_CACHE_SEED=random` takes a fresh seed range and prints it;
 * `RECORD_CACHE_WORLDS` and `RECORD_CACHE_OPS` resize the run.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { RecordContentCache } from '../cache';
import { CONTROLS, factoryFor } from './cache-controls';
import { deterministic, NO_BREAKS, runWorld, type Clock, type ModelBreaks } from './reference-model';

const clock: Clock = { set: (ms) => vi.setSystemTime(ms) };
const WORLDS = Number(process.env.RECORD_CACHE_WORLDS ?? 200);
const OPS = Number(process.env.RECORD_CACHE_OPS ?? 1500);
const SEED =
  process.env.RECORD_CACHE_SEED === 'random' ? Math.floor(Math.random() * 1_000_000_000) : Number(process.env.RECORD_CACHE_SEED ?? 1);

beforeAll(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  if (process.env.RECORD_CACHE_SEED === 'random') console.log(`RECORD_CACHE_SEED=${SEED} worlds=${WORLDS} ops=${OPS}`);
});
afterAll(() => {
  vi.useRealTimers();
});

const real = factoryFor(undefined);

/**
 * Scans deterministic cases and worlds for a violation of the invariant `named`. A broken part can trip
 * a neighbouring invariant first at some seeds (a wrong tag shows wrong bytes before it names two
 * byte sequences), so the scan goes on until the named one is seen; with none seen it returns the
 * first violation of any kind, which fails the caller's check on the name.
 */
async function violationOf(make: typeof real, model: ModelBreaks, worlds: number, seed: number, named: string): Promise<string | null> {
  let other: string | null = null;
  const seen = (text: string): string | null => {
    if (text.includes(named)) return text;
    other ??= text;
    return null;
  };
  for (const d of await deterministic(make, clock, model)) {
    if (!d.ok) {
      const hit = seen(`${d.name}: ${d.msg}`);
      if (hit) return hit;
    }
  }
  for (let s = seed; s < seed + worlds; s++) {
    const { world } = await runWorld({ seed: s, ops: OPS, makeCache: make, clock, breaks: model });
    for (const v of world.violations) {
      const hit = seen(`seed ${s}: ${v}`);
      if (hit) return hit;
    }
  }
  let refetch = 0;
  for (let s = seed; s < seed + Math.min(worlds, 50); s++) {
    const { world } = await runWorld({ seed: s, ops: OPS, makeCache: make, clock, breaks: model, contentrevWorld: true });
    for (const v of world.violations) {
      const hit = seen(`reindex world seed ${s}: ${v}`);
      if (hit) return hit;
    }
    refetch += world.refetchSameBytes;
  }
  if (refetch) {
    const hit = seen(`I11 ${refetch} full refetches of unchanged bytes`);
    if (hit) return hit;
  }
  return other;
}

describe('record content cache against the reference model', () => {
  it('holds every invariant on the product cache', { timeout: 600_000 }, async () => {
    const det = await deterministic(real, clock);
    for (const d of det) expect(d.ok, `${d.name}: ${d.msg}`).toBe(true);
    let opens = 0;
    let hits = 0;
    let retargets = 0;
    let inplace = 0;
    let n304 = 0;
    for (let s = SEED; s < SEED + WORLDS; s++) {
      const { world, stats } = await runWorld({ seed: s, ops: OPS, makeCache: real, clock });
      expect(world.violations, `seed ${s}`).toEqual([]);
      opens += stats.opens;
      hits += stats.labelHits;
      retargets += stats.retarget;
      inplace += stats.inplace;
      n304 += world.n304;
    }
    expect(opens).toBeGreaterThan(0);
    expect(n304).toBeGreaterThan(0);
    // the worlds reach the paths the invariants are about
    expect(hits).toBeGreaterThan(0);
    expect(retargets).toBeGreaterThan(0);
    expect(inplace).toBeGreaterThan(0);
  });

  it('answers 304 for every revalidation of unchanged bytes after a re-index', { timeout: 300_000 }, async () => {
    let refetch = 0;
    for (let s = SEED; s < SEED + Math.min(WORLDS, 20); s++) {
      const { world } = await runWorld({ seed: s, ops: OPS, makeCache: real, clock, contentrevWorld: true });
      expect(world.violations, `seed ${s}`).toEqual([]);
      refetch += world.refetchSameBytes;
    }
    expect(refetch).toBe(0);
  });

  it('has the 26 controls', () => {
    expect(CONTROLS).toHaveLength(26);
    expect(new Set(CONTROLS.map((c) => c.name)).size).toBe(26);
  });

  for (const control of CONTROLS) {
    it(`control ${control.name} fails ${control.breaks}`, { timeout: 300_000 }, async () => {
      const model = { ...NO_BREAKS, ...control.model };
      const found = await violationOf(factoryFor(control.cache), model, Math.max(WORLDS, 20), SEED, control.breaks);
      expect(found, `control ${control.name} was not caught`).not.toBeNull();
      expect(found!).toContain(control.breaks);
    });
  }

  it('the product cache class is what the controls extend', () => {
    expect(real({ fetcher: async () => ({ status: 304 }) }) instanceof RecordContentCache).toBe(true);
  });
});
