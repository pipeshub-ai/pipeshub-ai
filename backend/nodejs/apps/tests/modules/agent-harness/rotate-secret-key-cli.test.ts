/** ADR-KEY-01: the operator command's arguments, exit codes and output (KC-1..KC-6), and its Python step (KC-7..KC-9). */
import { expect } from 'chai';
import { randomBytes } from 'node:crypto';
import {
  parseRotateArgs,
  rotationRing,
  runRotateSecretKeyCli,
  type RotateCliBackend,
} from '../../../src/modules/agent-harness/ops/rotate-secret-key-cli';
import {
  EgressStateRekeyStep,
  SyncPointRekeyStep,
  type RunProcess,
} from '../../../src/modules/agent-harness/ops/egress-rekey-step';
import {
  SecretKeyRotation,
  emptyReport,
  type RotationStep,
  type StepContext,
} from '../../../src/modules/agent-harness/application/secret-key-rotation';
import type { LeaderLease } from '../../../src/modules/agent-harness/ports/leader-lease';

const lease = (): LeaderLease => ({ tryAcquire: async () => true, release: async () => undefined });

const step = (previous: number, apply: { moved: boolean } = { moved: false }): RotationStep => ({
  name: 'fake',
  run: async (context: StepContext) => {
    const report = emptyReport('fake');
    report.total = 1;
    if (apply.moved || previous === 0) report.current = 1;
    else if (context.apply) {
      apply.moved = true;
      report.moved = 1;
      report.current = 1;
    } else report.previous = previous;
    context.progress(report);
    return report;
  },
});

const backend = (steps: RotationStep[], other: 'previous' | 'next' | null = 'previous') => {
  const state = { closed: false };
  const made: RotateCliBackend = {
    rotation: (parsed, onProgress) =>
      new SecretKeyRotation({
        steps,
        ring: { other },
        lease: lease(),
        holder: 'h',
        ...(parsed.acceptUnreadable !== undefined ? { acceptUnreadable: parsed.acceptUnreadable } : {}),
        onProgress,
      }),
    close: async () => {
      state.closed = true;
    },
  };
  return { made, state };
};

async function cli(argv: string[], made: RotateCliBackend | Error) {
  const out: string[] = [];
  const err: string[] = [];
  const saved = process.env.DOC_STORE;
  process.env.DOC_STORE = 'postgres';
  try {
    const code = await runRotateSecretKeyCli(
      argv,
      async () => {
        if (made instanceof Error) throw made;
        return made;
      },
      (line) => out.push(line),
      (line) => err.push(line),
    );
    return { code, out, err };
  } finally {
    if (saved === undefined) delete process.env.DOC_STORE;
    else process.env.DOC_STORE = saved;
  }
}

describe('ADR-KEY-01: rotate-secret-key-cli', () => {
  it('KC-1: the mode and flags are checked; a usage error exits 2', async () => {
    expect(parseRotateArgs(['rotate', '--accept-unreadable', '3']).acceptUnreadable).to.equal(3);
    expect(parseRotateArgs(['dry-run', '--python', 'python3', '--python-dir', '/x']).options).to.deep.equal({
      python: 'python3',
      'python-dir': '/x',
    });
    for (const argv of [[], ['spin'], ['rotate', '--bogus', '1'], ['rotate', '--python'], ['rotate', '--accept-unreadable', '0']]) {
      expect(() => parseRotateArgs(argv)).to.throw();
      expect((await cli(argv, backend([]).made)).code).to.equal(2);
    }
  });

  it('KC-2: on MongoDB the store options are required', async () => {
    const saved = process.env.DOC_STORE;
    process.env.DOC_STORE = 'mongo';
    try {
      const err: string[] = [];
      const code = await runRotateSecretKeyCli(['verify'], async () => backend([]).made, () => undefined, (l) => err.push(l));
      expect(code).to.equal(2);
      expect(err).to.deep.equal(['missing --mongo-uri, --db']);
    } finally {
      if (saved === undefined) delete process.env.DOC_STORE;
      else process.env.DOC_STORE = saved;
    }
  });

  it('KC-3: rotate prints progress and one result line, exits 0 and closes the stores', async () => {
    const { made, state } = backend([step(2)]);
    const run = await cli(['rotate'], made);
    expect(run.code).to.equal(0);
    const lines = run.out.map((line) => JSON.parse(line) as Record<string, unknown>);
    expect(lines.filter((l) => l['event'] === 'progress').length).to.be.greaterThan(0);
    const result = lines.at(-1)!;
    expect(result).to.include({ event: 'result', mode: 'rotate', ok: true, refused: null });
    expect(state.closed).to.equal(true);
  });

  it('KC-4: verify with data left exits 1; retire refused names why on stderr; retire ok says what to do next', async () => {
    expect((await cli(['verify'], backend([step(1)]).made)).code).to.equal(1);
    const refused = await cli(['retire'], backend([step(1)]).made);
    expect(refused.code).to.equal(1);
    expect(refused.err.join('\n')).to.match(/cannot be retired yet/);
    const ok = await cli(['retire'], backend([step(0)]).made);
    expect(ok.code).to.equal(0);
    expect(JSON.parse(ok.out.at(-1)!)).to.include({ next: 'remove SECRET_KEY_PREVIOUS from every service and restart them' });
  });

  it('KC-5: a ring the services would refuse fails before anything runs (exit 1); a step that throws exits 1 and still closes', async () => {
    const ring = await cli(['rotate'], new Error('SECRET_KEY_PREVIOUS is the same as SECRET_KEY: a rotation needs a new key'));
    expect(ring.code).to.equal(1);
    expect(ring.err).to.deep.equal(['SECRET_KEY_PREVIOUS is the same as SECRET_KEY: a rotation needs a new key']);
    const throwing: RotationStep = { name: 'x', run: async () => Promise.reject(new Error('store down')) };
    const { made, state } = backend([throwing]);
    const failed = await cli(['dry-run'], made);
    expect(failed.code).to.equal(1);
    expect(failed.err).to.deep.equal(['store down']);
    expect(state.closed).to.equal(true);
  });

  it('KC-6: the output carries counts only: no key, digest or value of the ring', async () => {
    const key = randomBytes(32).toString('hex');
    const saved = { ...process.env };
    try {
      process.env.SECRET_KEY = key;
      const run = await cli(['dry-run'], backend([step(3)]).made);
      expect([...run.out, ...run.err].join('\n')).not.to.include(key);
      for (const line of run.out) {
        const parsed = JSON.parse(line) as Record<string, unknown>;
        const text = JSON.stringify(parsed);
        expect(text).not.to.match(/[0-9a-f]{32}/);
      }
    } finally {
      process.env = saved;
    }
  });

  describe('the egress state step (python -m app.harness.sandbox.egress_rekey)', () => {
    const context = (apply: boolean): StepContext => ({ apply, checkpoint: () => undefined, progress: () => undefined });

    it('KC-7: runs the module, with --apply only when applying, and reads its counts', async () => {
      const calls: string[][] = [];
      const runner: RunProcess = async (_command, args) => {
        calls.push([...args]);
        return {
          code: 0,
          stdout: 'noise\n{"current":1,"draining":2,"moved":3,"previous":4,"step":"egress-state","total":10,"unreadable":0}\n',
        };
      };
      const step = new EgressStateRekeyStep({ executable: 'python', cwd: '/app/python' }, runner);
      const report = await step.run(context(true));
      expect(report).to.deep.equal({ step: 'egress-state', total: 10, current: 1, previous: 4, moved: 3, unreadable: 0, draining: 2 });
      await step.run(context(false));
      expect(calls).to.deep.equal([
        ['-m', 'app.harness.sandbox.egress_rekey', '--apply'],
        ['-m', 'app.harness.sandbox.egress_rekey'],
      ]);
    });

    it('KC-8: a failure is an error naming what to check, never the process output', async () => {
      const step = (outcome: Awaited<ReturnType<RunProcess>> | Error) =>
        new EgressStateRekeyStep({ executable: 'py', cwd: '/d' }, async () => {
          if (outcome instanceof Error) throw outcome;
          return outcome;
        });
      const failure = async (s: EgressStateRekeyStep) => (await s.run(context(false)).catch((e: Error) => e)) as Error;
      expect((await failure(step(new Error('ENOENT')))).message).to.match(/could not start py in \/d .*--python and --python-dir/);
      expect((await failure(step({ code: 1, stdout: 'garbage' }))).message).to.equal('the egress state step printed no report (exit 1)');
      expect((await failure(step({ code: 1, stdout: '{"step":"egress-state","error":"RedisError"}' }))).message).to.equal(
        'the egress state step failed: RedisError',
      );
      expect((await failure(step({ code: 2, stdout: '{}' }))).message).to.equal('the egress state step failed: exit 2');
      expect((await failure(step({ code: 0, stdout: '{"total":1}' }))).message).to.match(/no count "current"/);
      expect((await failure(step({ code: 0, stdout: '{"total":-1,"current":0}' }))).message).to.match(/no count "total"/);
    });

    it('KC-10 (WP87-FU-4b): the sync point step runs its module, and retire refuses while a sync point is under the previous key', async () => {
      const calls: string[][] = [];
      const counts = (previous: number) =>
        `{"current":${String(3 - previous)},"draining":0,"moved":0,"previous":${String(previous)},"step":"sync-points","total":3,"unreadable":0}\n`;
      let previous = 1;
      const syncPoints = new SyncPointRekeyStep({ executable: 'python', cwd: '/app/python' }, async (_command, args) => {
        calls.push([...args]);
        return { code: 0, stdout: counts(previous) };
      });
      expect(syncPoints.name).to.equal('sync-points');
      const refused = await cli(['retire'], backend([syncPoints]).made);
      expect(refused.code).to.equal(1);
      expect(refused.err.join('\n')).to.match(/cannot be retired yet/);
      previous = 0;
      expect((await cli(['retire'], backend([syncPoints]).made)).code).to.equal(0);
      expect(calls[0]).to.deep.equal(['-m', 'app.connectors.core.base.sync_point.rekey']);
      const failed = new SyncPointRekeyStep({ executable: 'py', cwd: '/d' }, async () => ({
        code: 1,
        stdout: '{"step":"sync-points","error":"GraphQueryError"}',
      }));
      expect(((await failed.run(context(false)).catch((e: Error) => e)) as Error).message).to.equal(
        'the sync point step failed: GraphQueryError',
      );
    });

    it('KC-9: the real runner starts a process and returns its stdout and exit code', async () => {
      const step = new EgressStateRekeyStep({ executable: process.execPath, cwd: process.cwd() });
      // node -m … is not a node option: node exits 9 printing its usage on stderr, nothing on stdout.
      const error = (await step.run(context(false)).catch((e: Error) => e)) as Error;
      expect(error.message).to.match(/printed no report \(exit 9\)/);
      const missing = new EgressStateRekeyStep({ executable: '/nonexistent/python-kr', cwd: process.cwd() });
      expect(((await missing.run(context(false)).catch((e: Error) => e)) as Error).message).to.match(/could not start/);
    });
  });

  it('KC-11: a rotate with no previous key is a cipher move only where writers seal with HKDF, else refused', async () => {
    const key = randomBytes(32).toString('hex');
    const alone = { current: key, other: null };
    expect(rotationRing(alone, { SECRET_KEY_KDF: 'hkdf' })).to.deep.equal({ other: null, cipherMove: true });
    expect(rotationRing(alone, { PIPESHUB_HARDENED: 'true' })).to.deep.equal({ other: null, cipherMove: true });
    expect(rotationRing(alone, {})).to.deep.equal({ other: null, cipherMove: false });
    expect(rotationRing({ current: key, other: { role: 'previous', secret: 'x' } }, {}).other).to.equal('previous');
    const refused = await new SecretKeyRotation({
      steps: [step(0)],
      ring: rotationRing(alone, { SECRET_KEY_KDF: 'sha256' }),
      lease: lease(),
      holder: 'op',
    }).run('rotate');
    expect(refused.refused).to.match(/^SECRET_KEY_PREVIOUS is not set/);
  });
});
