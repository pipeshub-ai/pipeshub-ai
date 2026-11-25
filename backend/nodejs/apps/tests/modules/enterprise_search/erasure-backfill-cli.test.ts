/**
 * E7B BF-19: the operator CLI over an in-memory backend: one JSON line, exit
 * 0 / 1 refused / 2 usage / 3 in progress, and `execute` only after its
 * preparation (transactions, indexes) passed.
 */
import { expect } from 'chai';
import { InMemoryConversations } from '../../../src/modules/agent-harness/adapters/in-memory-conversations';
import { InMemoryErasureBackfillStore } from '../../../src/modules/agent-harness/adapters/in-memory-erasure-backfill';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { ErasureBackfillService } from '../../../src/modules/agent-harness/application/erasure-backfill';
import { BackfillRefused } from '../../../src/modules/agent-harness/domain/erasure-backfill';
import {
  BackfillCliBackend,
  BackfillCliIo,
  runErasureBackfillCli,
} from '../../../src/modules/enterprise_search/ops/erasure-backfill-cli';
import { nextErasureStep } from '../../../src/modules/agent-harness/domain/session-erasure';

const ORG = 'aa0000000000000000000001';

function backend(options: { transactions?: boolean; jobsDone?: boolean } = {}) {
  let clock = 1_000;
  const conversations = new InMemoryConversations(() => clock);
  conversations.put({ orgId: ORG, sessionId: 'aa00000000000000000000a1', deleted: true, updatedAt: 1, lastActivityAt: 1 });
  const jobs = new InMemoryErasureJobStore(() => clock);
  const state = { prepared: 0, closed: 0, connected: [] as string[] };
  const service = new ErasureBackfillService<unknown>({
    source: conversations,
    store: new InMemoryErasureBackfillStore(() => clock),
    jobs,
    audit: new InMemoryAuditChainStore(),
    transaction: (work) => work(undefined),
    lease: { tryAcquire: async () => true, release: async () => undefined },
    holderId: 'cli',
    pollMs: 1_000,
    sleep: async (ms) => {
      clock += ms;
      if (options.jobsDone !== false) {
        for (;;) {
          let job = await jobs.claimDue('w', 60_000);
          if (job === null) break;
          // The runner walks every step; a job moves only to the step after its own (QA13-F3).
          while (job.step !== null) {
            const next = nextErasureStep(job.step);
            await jobs.advance(job, {}, next, 60_000);
            job = { ...job, step: next };
          }
        }
      }
    },
  });
  const connect = async (uri: string | undefined, db: string | undefined): Promise<BackfillCliBackend> => {
    state.connected.push(`${uri}/${db}`);
    return {
      service,
      prepareForExecute: async () => {
        state.prepared += 1;
        if (options.transactions === false) {
          throw new BackfillRefused('transactions_unavailable', 'needs a replica set');
        }
      },
      close: async () => {
        state.closed += 1;
      },
    };
  };
  return { connect, state };
}

function io() {
  const lines: string[] = [];
  const errors: string[] = [];
  const files: Record<string, string> = {};
  const sink: BackfillCliIo = {
    out: (line) => lines.push(line),
    err: (line) => errors.push(line),
    writeFile: (path, content) => {
      files[path] = content;
    },
  };
  return { sink, lines, errors, files };
}

const base = ['--mongo-uri', 'mongodb://x', '--db', 'pipeshub'];

describe('E7B erasure backfill CLI (BF-19)', () => {
  it('plan prints the dry run as one JSON line, writes the plan file, and exits 0', async () => {
    const { connect, state } = backend();
    const out = io();
    expect(await runErasureBackfillCli(['plan', ...base, '--out', '/tmp/plan.json'], connect, out.sink)).to.equal(0);
    const report = JSON.parse(out.lines[0]!);
    expect(report).to.include({ command: 'plan' });
    expect(report.digest).to.match(/^sha256:/);
    expect(report.orgs[0]).to.include({ orgId: ORG, sessions: 1 });
    expect(out.files['/tmp/plan.json']).to.equal(`${out.lines[0]!}\n`);
    expect(state.prepared, 'the dry run prepares (writes) nothing').to.equal(0);
    expect(state.closed).to.equal(1);
  });

  it('execute needs --digest and --operator (exit 2, nothing connected)', async () => {
    const { connect, state } = backend();
    const out = io();
    expect(await runErasureBackfillCli(['execute', ...base, '--operator', 'ops'], connect, out.sink)).to.equal(2);
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', 'sha256:x'], connect, out.sink)).to.equal(2);
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', 'sha256:x', '--operator', 'bad name'], connect, out.sink)).to.equal(2);
    expect(await runErasureBackfillCli(['erase', ...base], connect, out.sink)).to.equal(2);
    expect(await runErasureBackfillCli(['plan', '--db', 'x'], connect, out.sink)).to.equal(2);
    expect(state.connected).to.deep.equal([]);
    expect(out.errors).to.have.length(5);
  });

  it('execute with the reviewed digest completes (exit 0); again is already_done (exit 0)', async () => {
    const { connect } = backend();
    const out = io();
    await runErasureBackfillCli(['plan', ...base], connect, out.sink);
    const { digest } = JSON.parse(out.lines[0]!);
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', digest, '--operator', 'ops@example'], connect, out.sink)).to.equal(0);
    expect(JSON.parse(out.lines[1]!)).to.include({ command: 'execute', status: 'completed' });
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', digest, '--operator', 'ops@example'], connect, out.sink)).to.equal(0);
    expect(JSON.parse(out.lines[2]!)).to.include({ status: 'already_done' });
    expect(await runErasureBackfillCli(['status', ...base, '--digest', digest], connect, out.sink)).to.equal(0);
    expect(JSON.parse(out.lines[3]!).plan).to.include({ state: 'completed' });
  });

  it('a wrong digest is refused (exit 1) with its reason code', async () => {
    const { connect } = backend();
    const out = io();
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', 'sha256:00', '--operator', 'ops'], connect, out.sink)).to.equal(1);
    expect(JSON.parse(out.lines[0]!)).to.include({ command: 'execute', refused: 'plan_digest_mismatch' });
  });

  it('without transactions execute is refused before anything runs (exit 1)', async () => {
    const { connect } = backend({ transactions: false });
    const out = io();
    await runErasureBackfillCli(['plan', ...base], connect, out.sink);
    const { digest } = JSON.parse(out.lines[0]!);
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', digest, '--operator', 'ops'], connect, out.sink)).to.equal(1);
    expect(JSON.parse(out.lines[1]!)).to.include({ refused: 'transactions_unavailable' });
  });

  it('while the worker has not finished, execute exits 3 (in progress)', async () => {
    const { connect } = backend({ jobsDone: false });
    const out = io();
    await runErasureBackfillCli(['plan', ...base], connect, out.sink);
    const { digest } = JSON.parse(out.lines[0]!);
    expect(await runErasureBackfillCli(['execute', ...base, '--digest', digest, '--operator', 'ops', '--wait-seconds', '3'], connect, out.sink)).to.equal(3);
    expect(JSON.parse(out.lines[1]!)).to.include({ status: 'in_progress' });
  });
});
