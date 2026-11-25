/** PERF-C: the watch that finds audit appends whose link did not run (PERFC-39, PERFC-42) and the order they are linked in. */
import { expect } from 'chai';
import fc from 'fast-check';
import { AuditLinkWatch } from '../../../src/modules/agent-harness/application/audit-link-watch';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  AUDIT_LINK_STALE_MS,
  auditEntryDigest,
  AuditHead,
  AuditUnlinked,
  linkOrder,
  unlinkedAgeMs,
  verifyAuditChain,
} from '../../../src/modules/agent-harness/domain/audit';
import { propertyRuns } from '../../support/harness/property-profile';
import { auditEntry } from './support/audit-chain-store-contract';

const AT = Date.parse('2026-10-12T12:00:00.000Z');
const ago = (ms: number): string => new Date(AT - ms).toISOString();

type Line = [level: string, message: string, meta: Record<string, unknown>];

/** A chain whose reads link every org but the ones named stuck. */
const chainWith = (unlinked: AuditUnlinked[], stuck: string[] = []) => {
  let left = [...unlinked];
  const asked: string[] = [];
  return {
    asked,
    chain: {
      unlinked: async () => [...left],
      head: async (orgId: string): Promise<AuditHead | null> => {
        asked.push(orgId);
        if (!stuck.includes(orgId)) left = left.filter((each) => each.orgId !== orgId);
        return null;
      },
    },
  };
};

const watching = (chain: ReturnType<typeof chainWith>['chain']) => {
  const lines: Line[] = [];
  const seen: Array<[number, number]> = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
  const watch = new AuditLinkWatch({ chain, metrics: { unlinked: (appends, age) => seen.push([appends, age]) }, logger, now: () => AT });
  return { watch, lines, seen };
};

describe('PERFC-42: the audit link watch', () => {
  it('asks every org with an unlinked append for its link and reports none left', async () => {
    const { chain, asked } = chainWith([
      { orgId: 'org-1', appends: 2, oldestTs: ago(5_000) },
      { orgId: 'org-2', appends: 1, oldestTs: ago(90_000) },
    ]);
    const { watch, lines, seen } = watching(chain);
    expect(await watch.tick()).to.deep.equal([]);
    expect(asked).to.deep.equal(['org-1', 'org-2']);
    expect(seen).to.deep.equal([[0, 0]]);
    expect(lines).to.deep.equal([]);
  });

  it('reports what stays unlinked: the count, the oldest age, and an error for an org past the threshold', async () => {
    const { chain } = chainWith(
      [
        { orgId: 'org-young', appends: 1, oldestTs: ago(AUDIT_LINK_STALE_MS - 1) },
        { orgId: 'org-stuck', appends: 3, oldestTs: ago(AUDIT_LINK_STALE_MS) },
      ],
      ['org-young', 'org-stuck'],
    );
    const { watch, lines, seen } = watching(chain);
    expect((await watch.tick()).map((each) => each.orgId)).to.deep.equal(['org-young', 'org-stuck']);
    expect(seen).to.deep.equal([[4, AUDIT_LINK_STALE_MS / 1000]]);
    expect(lines).to.deep.equal([
      [
        'error',
        'audit.link_stale',
        { event: 'audit.link_stale', orgId: 'org-stuck', appends: 3, oldestAgeS: AUDIT_LINK_STALE_MS / 1000, choice: 'anchor_held_back' },
      ],
    ]);
  });

  it('a store that cannot answer is logged, not thrown; the next tick looks again', async () => {
    let fail = true;
    const { watch, lines, seen } = watching({
      unlinked: async () => {
        if (fail) throw new Error('store away');
        return [];
      },
      head: async () => null,
    });
    expect(await watch.tick()).to.deep.equal([]);
    expect(lines).to.deep.equal([
      ['error', 'audit.link_watch_failed', { event: 'audit.link_watch_failed', errorType: 'Error' }],
    ]);
    expect(seen).to.deep.equal([]);
    fail = false;
    await watch.tick();
    expect(seen).to.deep.equal([[0, 0]]);
  });

  it('one look at a time; start and stop own the interval and wait for a look under way', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    let looks = 0;
    const { watch } = watching({
      unlinked: async () => {
        looks += 1;
        await held;
        return [];
      },
      head: async () => null,
    });
    const first = watch.tick();
    expect(watch.tick()).to.equal(first);
    watch.start(60_000);
    watch.start(60_000);
    const stopped = watch.stop();
    release();
    await stopped;
    // A look asks twice: what is unlinked, and what is left after asking for the links.
    expect(looks).to.equal(2);
    await watch.tick();
    expect(looks).to.equal(4);
  });

  it('an unlinked append is as old as its time says, never negative', () => {
    expect(unlinkedAgeMs({ orgId: 'o', appends: 1, oldestTs: ago(1_500) }, AT)).to.equal(1_500);
    expect(unlinkedAgeMs({ orgId: 'o', appends: 1, oldestTs: ago(-10) }, AT)).to.equal(0);
  });
});

describe('PERFC-43: what the link may add to an entry, and the order it links in', () => {
  it('the digest taken at the commit covers everything the entry says and nothing of its place in the chain', () => {
    const entry = auditEntry(4);
    const digest = auditEntryDigest('org-1', ago(0), entry);
    expect(digest).to.match(/^sha256:[0-9a-f]{64}$/);
    expect(auditEntryDigest('org-1', ago(0), { ...entry })).to.equal(digest);
    const changed = [
      auditEntryDigest('org-2', ago(0), entry),
      auditEntryDigest('org-1', ago(1), entry),
      ...(
        [
          { kind: 'run.failed' },
          { sessionId: 'ses-2' },
          { streamId: 'ses-2' },
          { runId: 'run-2' },
          { journalSeq: 5 },
          { writer: 'worker' },
          { attemptId: 'attempt-1' },
          { fence: 2 },
          { fields: { ...entry.fields, principalRef: 'p_someone-else' } },
          { dataDigest: `sha256:${'e'.repeat(64)}` },
        ] as const
      ).map((change) => auditEntryDigest('org-1', ago(0), { ...entry, ...change })),
    ];
    expect(new Set([digest, ...changed]).size, 'every field changes it').to.equal(changed.length + 1);
  });

  it('appends are linked by their time; a stream whose times run against its journal order is put right', () => {
    const append = (streamId: string, journalSeq: number, ts: string) => ({ streamId, journalSeq, ts });
    expect(
      linkOrder([append('b', 1, ago(10)), append('a', 2, ago(30)), append('a', 1, ago(20)), append('c', 1, ago(40))]),
    ).to.deep.equal([append('c', 1, ago(40)), append('a', 1, ago(20)), append('a', 2, ago(30)), append('b', 1, ago(10))]);
  });

  it('property: whatever the times, every stream is in journal order and nothing is lost', () => {
    const appends = fc.array(
      fc.record({ streamId: fc.constantFrom('a', 'b', 'c'), ts: fc.integer({ min: 0, max: 5 }).map((n) => ago(n)) }),
      { maxLength: 30 },
    );
    fc.assert(
      fc.property(appends, (drawn) => {
        const next = new Map<string, number>();
        const read = drawn.map((each) => {
          const journalSeq = (next.get(each.streamId) ?? 0) + 1;
          next.set(each.streamId, journalSeq);
          return { ...each, journalSeq };
        });
        const ordered = linkOrder(read);
        expect(ordered).to.have.deep.members(read);
        const last = new Map<string, number>();
        for (const each of ordered) {
          expect(each.journalSeq).to.equal((last.get(each.streamId) ?? 0) + 1);
          last.set(each.streamId, each.journalSeq);
        }
      }),
      propertyRuns(500),
    );
  });

  it('the verifier names unlinked appends beside the chain, not as a problem of it', () => {
    expect(verifyAuditChain([], { unlinked: 3 })).to.deep.equal({ ok: true, records: 0, lastSeq: 0, problems: [], unlinked: 3 });
    expect(verifyAuditChain([]).unlinked).to.equal(0);
  });
});
