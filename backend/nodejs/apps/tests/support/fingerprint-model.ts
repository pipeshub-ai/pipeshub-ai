/**
 * TypeScript port of the step semantics of the reference model
 * (tests/storage/fingerprint-model/model.py, design mechanism only). The interleaving test replays the schedules `model.py emit`
 * writes against the real stack and compares the real `trusted` of every read with the expectation the model emitted; this port
 * re-derives that expectation itself, so a drift between the port and the model fails the test instead of passing silently.
 */
export const W = 10;
export const TICK = 6;

export interface Schedule {
  id: string;
  writers: Record<string, string[]>;
  readers: string[];
  hb: Record<string, string[]>;
  ticks: number;
  order: [string, number][];
  compliant: boolean;
  expected_strong: Record<string, { trusted: boolean; value: [number, string] | null }>;
  expected_coarse: Record<string, { trusted: boolean; value: null }>;
}

type Snap = { seq: number; tok: Record<string, number>; now: number };
interface Read {
  r1: Snap;
  c?: string;
  et?: string;
  trusted?: boolean;
  val?: [number, string];
}

/** The `order` of a schedule with the final reader's three steps appended, as the model runs it. */
export const finish = (order: [string, number][]): [string, number][] => [...order, ['F', 0], ['F', 1], ['F', 2]];

export function runModel(s: Pick<Schedule, 'writers' | 'hb' | 'order'>, em: 'strong' | 'coarse'): {
  reads: Record<string, Read>;
  compliant: boolean;
} {
  const S = { seq: 0, tok: {} as Record<string, number>, now: 0, content: 'c0' };
  const live = new Set<string>();
  const done: Record<string, number> = {};
  const pend: Record<string, number> = {};
  const ideal: Record<string, number> = {};
  const reads: Record<string, Read> = {};
  let compliant = true;
  const steps = s.writers;
  const crashed = (w: string): boolean =>
    live.has(w) && (done[w] ?? 0) >= (steps[w] ?? []).length && !(steps[w] ?? []).slice(0, done[w] ?? 0).includes('E1');
  const alive = (w: string): boolean => live.has(w) && !crashed(w);
  const settled = (snap: Snap): boolean => !Object.values(snap.tok).some((u) => u > snap.now);
  const snap = (): Snap => ({ seq: S.seq, tok: { ...S.tok }, now: S.now });
  for (const [actor, idx] of s.order) {
    if (actor === 'T') {
      S.now += TICK;
    } else if (actor in steps) {
      const st = (steps[actor] as string[])[idx] as string;
      done[actor] = (done[actor] ?? 0) + 1;
      if (st === 'B1') {
        S.tok = Object.fromEntries(Object.entries(S.tok).filter(([, u]) => u >= S.now));
      } else if (st === 'B2') {
        live.add(actor);
        ideal[actor] = S.now + W;
        S.seq += 1;
        S.tok[actor] = S.now + W;
      } else if (st === 'BY') {
        S.content = actor;
      } else {
        S.seq += 1; // E1 / E2
        delete S.tok[actor];
        if (st === 'E1') {
          live.delete(actor);
          delete ideal[actor];
        }
      }
    } else if (actor.startsWith('H')) {
      const w = actor[1] as string;
      const kind = (s.hb[actor] as string[])[idx];
      if (kind === 'H1') {
        pend[w] = S.now + W;
      } else {
        if (w in ideal) ideal[w] = Math.max(ideal[w] as number, pend[w] as number);
        if (w in S.tok) S.tok[w] = Math.max(S.tok[w] as number, pend[w] as number);
      }
    } else if (idx === 0) {
      reads[actor] = { r1: snap() };
    } else if (idx === 1) {
      const d = reads[actor] as Read;
      d.c = S.content;
      d.et = em === 'strong' ? S.content : 'same';
    } else {
      const d = reads[actor] as Read;
      const r3 = snap();
      d.trusted = settled(d.r1) && settled(r3) && d.r1.seq === r3.seq;
      d.val = [d.r1.seq, d.et as string];
    }
    for (const w of live) if (alive(w) && !((ideal[w] ?? 0) > S.now)) compliant = false;
  }
  return { reads, compliant };
}
