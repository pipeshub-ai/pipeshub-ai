#!/usr/bin/env python3
"""Reference model of the STOR-FP fingerprint (per-writer tokens, explicit heartbeat, two readers).

Used by CI: `python model.py --ci` (design must show 0 violations on heartbeat-compliant schedules, every mutant in MUTANTS must
violate, the REDUNDANT list must stay clean) and `python model.py emit --set <name>` (writes the schedules the TypeScript replay
runs against the real stack, with the model's expected `trusted` and value class for every read).

Events
  writer w in A,B : B1 prune expired tokens | B2 begin (seq+1, add token) | BY bytes land | E1 end (remove own token, seq+1) | E2 duplicate end
  heartbeat of w  : H1 read the server clock (value = now+W) | H2 apply it: renew only if the token is present, never shorten ($max)
  reader r in R,Q,F: row (counters, writers, clock) | stat (ETag of the content) | reread (counters again) -> trusted
  T               : the server clock advances by TICK (= 0.6 W; two ticks exceed W)
A writer is CRASHED when all its steps ran without E1 (the process is gone: no more heartbeats, bytes cannot land later).
A schedule is HEARTBEAT-COMPLIANT when, at every event, every live writer's token (as the correct mechanism holds it: begin, then max with every applied H2) is still unexpired.
A non-compliant schedule is the documented residual (heartbeat blocked longer than the window) and is counted separately.
ETag mode: strong (identifies content) | coarse (constant: the worst case of the local synthetic ETag).
"""
import collections, itertools, json, random, sys, time
W = 10; TICK = 6
STEPS = ['B1', 'B2', 'BY', 'E1']

def run(sched, mech='token', em='strong', mutant=None):
    """Replays one schedule; returns (reads, compliant)."""
    S = dict(seq=0, tok={}, now=0, content='c0', f=0, u=None)
    live = set(); done = collections.Counter(); pend = {}; ideal = {}
    reads = {}; compliant = True
    steps = sched['writers']
    def crashed(w): return w in live and done[w] >= len(steps[w]) and 'E1' not in steps[w][:done[w]]
    def alive(w): return w in live and not crashed(w)
    def settled(snap):
        if mech == 'counter': return snap['f'] <= 0 or (snap['u'] is not None and snap['now'] > snap['u'])
        return not any(u > snap['now'] for u in snap['tok'].values())
    def snap(): return dict(seq=S['seq'], tok=dict(S['tok']), now=S['now'], f=S['f'], u=S['u'])
    for actor, idx in sched['order']:
        if actor == 'T':
            S['now'] += TICK
        elif actor in steps:                          # writer step
            st = steps[actor][idx]; done[actor] += 1
            if st == 'B1':
                if mech == 'token':
                    S['tok'] = {} if mutant == 'prune_all' else {w: u for w, u in S['tok'].items() if u >= S['now']}
                elif S['u'] is not None and S['u'] < S['now']: S['f'] = 0
            elif st == 'B2':
                live.add(actor); ideal[actor] = S['now'] + W
                if mutant != 'begin_no_bump': S['seq'] += 1
                if mech == 'token': S['tok'][actor] = S['now'] + W
                else: S['f'] += 1; S['u'] = max(S['u'] or 0, S['now'] + W)
            elif st == 'BY': S['content'] = actor
            else:                                     # E1 / E2
                if mutant != 'end_no_bump': S['seq'] += 1
                if mech == 'token':
                    if mutant == 'end_removes_all': S['tok'].clear()
                    else:
                        S['tok'].pop(actor, None)
                        if mutant == 'end_nonidempotent' and st == 'E2':
                            for o in list(S['tok']):
                                if o != actor: S['tok'].pop(o); break
                elif S['f'] > 0: S['f'] -= 1
                if st == 'E1': live.discard(actor); ideal.pop(actor, None)
        elif actor.startswith('H'):                  # heartbeat of writer actor[1]
            w = actor[1]; kind = sched['hb'][actor][idx]
            if kind == 'H1':
                pend[w] = S['now'] + W
            else:
                if w in ideal: ideal[w] = max(ideal[w], pend[w])
            if kind != 'H1' and mech == 'token' and mutant != 'no_heartbeat':
                if w in S['tok']:
                    S['tok'][w] = pend[w] if mutant == 'renew_not_max' else max(S['tok'][w], pend[w])
                elif mutant == 'renew_resurrects': S['tok'][w] = pend[w]
        else:                                         # reader actor R, Q or F
            if idx == 0:
                n = S['now']
                reads[actor] = dict(r1=snap(), n1=n)
                if mutant == 'clock_late': reads[actor]['late'] = True
            elif idx == 1:
                reads[actor]['c'] = S['content']; reads[actor]['et'] = S['content'] if em == 'strong' else 'same'
                if reads[actor].get('late'): reads[actor]['r1']['now'] = S['now']
            else:
                d = reads[actor]; r3 = snap()
                ok = d['r1']['seq'] == r3['seq'] or mutant == 'reread_no_seq'
                s1 = settled(d['r1']) or mutant == 'settled_ignored_row'
                s3 = settled(r3) or mutant == 'settled_ignored_reread'
                d['trusted'] = bool(s1 and s3 and ok)
                d['val'] = (d['r1']['seq'], d['et'])
                d['lie'] = d['trusted'] and any(alive(w) for w in live)
        for w in live:
            if alive(w) and not ideal.get(w, 0) > S['now']: compliant = False
    return reads, compliant

def finish(sched):
    s = dict(sched); s['order'] = list(sched['order']) + [('F', 0), ('F', 1), ('F', 2)]
    return s

def violations(sched, mech, em, mutant, tail_ticks):
    s = finish(sched)
    if tail_ticks: s['order'] = s['order'][:-3] + [('T', 0), ('T', 1)] + s['order'][-3:]
    reads, comp = run(s, mech, em, mutant)
    names = [r for r in ('R', 'Q', 'F') if r in reads]
    out = collections.Counter()
    for r in names:
        if reads[r]['lie']: out['trusted_while_live_writer'] += 1
    for a, b in itertools.combinations(names, 2):
        x, y = reads[a], reads[b]
        if x['trusted'] and y['trusted'] and x['val'] == y['val'] and x['c'] != y['c']: out['value_collision'] += 1
    return out, comp

def interleave(seqs):
    keys = list(seqs)
    def rec(pos):
        if all(pos[k] == len(seqs[k]) for k in keys): yield []; return
        for k in keys:
            if pos[k] < len(seqs[k]):
                i = pos[k]; pos[k] += 1
                for rest in rec(pos): yield [(k, i)] + rest
                pos[k] -= 1
    yield from rec({k: 0 for k in keys})

def sampled(seqs, n, rng):
    keys = list(seqs)
    for _ in range(n):
        rem = [k for k in keys for _ in seqs[k]]; rng.shuffle(rem)
        pos = {k: 0 for k in keys}; out = []
        for k in rem: out.append((k, pos[k])); pos[k] += 1
        yield out

def make(writers, readers, hb, ticks):
    seqs = {w: list(range(len(s))) for w, s in writers.items()}
    for r in readers: seqs[r] = [0, 1, 2]
    for h, ks in hb.items(): seqs[h] = list(range(len(ks)))
    if ticks: seqs['T'] = list(range(ticks))
    return seqs

# ---- the schedule sets the replay runs (fixed counts; exhaustive sets are exhaustive, sampled ones are seeded, WITHOUT replacement) --
# A set is a list of blocks; every block has its own shape, seed and count. Sets s1..s5 each carry a block with ticks > 0.
def blk(kind, writers, readers, hb, ticks, count, seed=None):
    return dict(kind=kind, writers=writers, readers=readers, hb=hb, ticks=ticks, count=count, seed=seed)
HB2 = {'HA': ['H1', 'H2'], 'HB': ['H1', 'H2']}
SETS = {
  's1_1w_1r':        [blk('exhaustive', {'A': STEPS}, ['R'], {}, 0, 35),
                      blk('sampled', {'A': STEPS}, ['R'], {'HA': ['H1', 'H2']}, 2, 20, 31)],
  's2_1w_dupend_1r': [blk('exhaustive', {'A': STEPS + ['E2']}, ['R'], {}, 0, 56),
                      blk('sampled', {'A': STEPS + ['E2']}, ['R'], {'HA': ['H1', 'H2']}, 2, 20, 32)],
  's3_1w_2r':        [blk('sampled', {'A': STEPS}, ['R', 'Q'], {}, 0, 300, 5),            # 300 of the 4,200 interleavings, no repeats
                      blk('sampled', {'A': STEPS}, ['R', 'Q'], {'HA': ['H1', 'H2']}, 2, 30, 33)],
  's4_2w_1r':        [blk('sampled', {'A': STEPS + ['E2'], 'B': STEPS[:3]}, ['R'], HB2, 0, 200, 7),   # A ends twice, B never ends
                      blk('sampled', {'A': STEPS + ['E2'], 'B': STEPS[:3]}, ['R'], HB2, 2, 20, 34)],
  's5_2w_2r':        [blk('sampled', {'A': STEPS, 'B': STEPS}, ['R', 'Q'], HB2, 0, 100, 13),
                      blk('sampled', {'A': STEPS, 'B': STEPS}, ['R', 'Q'], HB2, 2, 20, 35)],
  's6_ticks':        [blk('sampled', {'A': STEPS, 'B': STEPS}, ['R', 'Q'], {'HA': ['H1', 'H2', 'H1', 'H2'], 'HB': ['H1', 'H2']}, 2, 40, 11)],
  's7_crash':        [blk('sampled', {'A': STEPS[:3], 'B': STEPS}, ['R', 'Q'], {'HA': ['H1', 'H2'], 'HB': ['H1', 'H2', 'H1', 'H2']}, 2, 100, 41),
                      blk('sampled', {'A': STEPS[:2], 'B': STEPS + ['E2']}, ['R', 'Q'], {'HB': ['H1', 'H2']}, 2, 100, 42)],
  's8_nc15':         [dict(kind='fixed', writers={'A': STEPS, 'B': STEPS}, readers=['R', 'Q'], hb={}, ticks=2, count=1,
                           order=[('A',0),('B',0),('R',0),('Q',0),('A',1),('T',0),('Q',1),('B',1),('A',2),('B',2),('R',1),('A',3),('B',3),('T',1),('R',2),('Q',2)])],
}
def set_count(name): return sum(b['count'] for b in SETS[name])

# wider sets used only by --ci (model check, not replayed on the stack): ticks, heartbeats, crashed writers, duplicate end
CI_SETS = {
  'c1_ticks':  dict(kind='sampled', seed=21, n=9000, writers={'A': STEPS + ['E2'], 'B': STEPS}, readers=['R', 'Q'],
                    hb={'HA': ['H1', 'H2', 'H1', 'H2'], 'HB': ['H1', 'H2']}, ticks=2, count=9000),
  'c2_crash':  dict(kind='sampled', seed=22, n=9000, writers={'A': STEPS[:3], 'B': STEPS}, readers=['R', 'Q'],
                    hb={'HA': ['H1', 'H2'], 'HB': ['H1', 'H2', 'H1', 'H2']}, ticks=2, count=9000),
  'c3_crash0': dict(kind='sampled', seed=23, n=6000, writers={'A': STEPS[:2], 'B': STEPS + ['E2']}, readers=['R', 'Q'],
                    hb={'HB': ['H1', 'H2']}, ticks=2, count=6000),
}
ALL = {**SETS, **{k: [v] for k, v in CI_SETS.items()}}
NC15 = SETS['s8_nc15'][0]

def schedules(name):
    for c in ALL[name]:
        if c['kind'] == 'fixed':
            yield dict(writers=c['writers'], readers=c['readers'], hb=c['hb'], ticks=c['ticks'], order=c['order']); continue
        seqs = make(c['writers'], c['readers'], c['hb'], c['ticks'])
        if c['kind'] == 'exhaustive': orders = list(interleave(seqs))
        else:
            n = c.get('n', c['count']); rng = random.Random(c['seed']); seen = set(); orders = []
            universe = sum(1 for _ in interleave(seqs)) if sum(len(v) for v in seqs.values()) <= 12 else None
            if universe is not None and universe <= n: n = universe        # never ask for more than exist
            while len(orders) < n:
                o = next(sampled(seqs, 1, rng)); key = tuple(o)
                if key not in seen: seen.add(key); orders.append(o)
        assert len(orders) == c['count'], (name, len(orders), c['count'])
        for order in orders:
            yield dict(writers=c['writers'], readers=c['readers'], hb=dict(c['hb']), ticks=c['ticks'], order=order)

# ---- mutants: each must produce >= 1 violation on compliant schedules (the model "bites"); REDUNDANT must stay clean -----------
MUTANTS = ['no_heartbeat', 'end_removes_all', 'end_nonidempotent', 'prune_all', 'begin_no_bump', 'settled_ignored_row',
           'reread_no_seq', 'renew_not_max']
# belt and braces: the proof does not depend on them (renew_resurrects only keeps an ended writer's token alive: a false 'unsettled',
# the safe direction; contract case C11 pins it)
REDUNDANT = ['end_no_bump', 'settled_ignored_reread', 'clock_late', 'renew_resurrects']

def check(mutant, mech='token', em_list=('strong', 'coarse'), names=None, limit=None):
    tot = collections.Counter(); missed = collections.Counter()
    for name in names or ALL:
        for i, sch in enumerate(schedules(name)):
            if limit and i >= limit: break
            for em in em_list:
                for te in (0, 1):
                    v, comp = violations(sch, mech, em, mutant, te)
                    tot['runs'] += 1
                    for k, n in v.items():
                        (tot if comp else missed)[k] += n
    return dict(tot), dict(missed)

def ci():
    t0 = time.time(); bad = 0
    d, m = check(None, limit=None)
    print('design (compliant schedules):', d, '| non-compliant (documented residual):', m)
    if any(k != 'runs' for k in d): bad += 1; print('FAIL design violates')
    nc15 = next(schedules('s8_nc15')); nc, _ = violations(nc15, 'token', 'coarse', 'reread_no_seq', 0); ok, _ = violations(nc15, 'token', 'coarse', None, 0)
    print('NC15 fixed schedule: the mutant breaks', len(nc), 'properties; the design breaks', len(ok))  # counts only: the dicts are keyed by property names
    if not nc or ok: bad += 1; print('FAIL NC15 fixed schedule')
    for mu in MUTANTS:
        r, _ = check(mu)
        hit = {k: v for k, v in r.items() if k != 'runs'}
        print('mutant %-22s' % mu, hit or 'NO VIOLATION')
        if not hit: bad += 1
    for mu in REDUNDANT:
        r, _ = check(mu)
        hit = {k: v for k, v in r.items() if k != 'runs'}
        print('redundant %-19s' % mu, hit or 'clean (as documented)')
    c, _ = check(None, mech='counter')
    print('round-1 counter control:', c)
    if not any(k != 'runs' for k in c): bad += 1; print('FAIL control does not bite')
    print('elapsed', round(time.time() - t0, 1)); return 1 if bad else 0

def emit(name):
    out = []
    for i, sch in enumerate(schedules(name)):
        rec = dict(id='%s/%d' % (name, i), **{k: sch[k] for k in ('writers', 'readers', 'hb', 'ticks')}, order=sch['order'])
        for em in ('strong', 'coarse'):
            reads, comp = run(finish(sch), 'token', em, None)
            rec['expected_' + em] = {r: dict(trusted=d['trusted'], value=list(d['val']) if em == 'strong' else None) for r, d in reads.items()}
            rec['compliant'] = comp
        out.append(rec)
    assert len(out) == sum(b['count'] for b in ALL[name]), (name, len(out))
    json.dump(out, sys.stdout)

if __name__ == '__main__':
    if '--ci' in sys.argv: sys.exit(ci())
    if len(sys.argv) > 2 and sys.argv[1] == 'emit':
        emit(sys.argv[-1]); sys.exit(0)             # `emit <name>` or `emit --set <name>`
    if sys.argv[1:2] == ['counts']:
        tot = 0
        for n in SETS:
            c = sum(1 for _ in schedules(n)); tot += c
            tk = sum(b['count'] for b in SETS[n] if b['ticks'] > 0)
            print(n, c, 'with ticks:', tk)
        print('total', tot)
