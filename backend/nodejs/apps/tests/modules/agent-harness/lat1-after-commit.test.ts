/**
 * LAT1 NT-1, NT-6: what a transaction announces runs only after it commits,
 * in order, and never fails the commit; the Redis notifier's wire.
 */
import { expect } from 'chai';
import { AfterCommitPublisher } from '../../../src/libs/utils/after-commit';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  RedisRunReadyNotifier,
  runReadyChannel,
} from '../../../src/modules/agent-harness/adapters/redis/redis-run-ready';
import type { IRedisConnectionProvider } from '../../../src/libs/services/redis/connectionProvider.interface';

const recording = () => {
  const lines: Array<[string, Record<string, unknown> | undefined]> = [];
  const logger: HarnessLogger = {
    debug: () => undefined,
    info: () => undefined,
    warn: (message, meta) => void lines.push([message, meta]),
    error: (message, meta) => void lines.push([message, meta]),
  };
  return { lines, logger };
};

describe('LAT1 NT-1: AfterCommitPublisher', () => {
  it('runs nothing before the commit, then every publish in the order deferred', async () => {
    const commits = new AfterCommitPublisher<object>();
    const tx = {};
    const seen: string[] = [];
    commits.begin(tx);
    commits.defer(tx, async () => void seen.push('a'));
    commits.defer(tx, async () => void seen.push('b'));
    expect(seen).to.deep.equal([]);
    await commits.committed(tx);
    expect(seen).to.deep.equal(['a', 'b']);
    await commits.committed(tx);
    expect(seen, 'once').to.deep.equal(['a', 'b']);
  });

  it('a retried attempt drops what the earlier attempt deferred', async () => {
    const commits = new AfterCommitPublisher<object>();
    const tx = {};
    const seen: string[] = [];
    commits.begin(tx);
    commits.defer(tx, async () => void seen.push('first attempt'));
    commits.begin(tx);
    commits.defer(tx, async () => void seen.push('second attempt'));
    await commits.committed(tx);
    expect(seen).to.deep.equal(['second attempt']);
  });

  it('an aborted transaction publishes nothing', async () => {
    const commits = new AfterCommitPublisher<object>();
    const tx = {};
    const seen: string[] = [];
    commits.defer(tx, async () => void seen.push('x'));
    commits.discard(tx);
    await commits.committed(tx);
    expect(seen).to.deep.equal([]);
  });

  it('a failed publish is logged and never fails the commit; later ones still run', async () => {
    const { lines, logger } = recording();
    const commits = new AfterCommitPublisher<object>(logger);
    const tx = {};
    const seen: string[] = [];
    commits.defer(tx, async () => {
      throw new Error('redis down');
    });
    commits.defer(tx, async () => void seen.push('after'));
    await commits.committed(tx);
    expect(seen).to.deep.equal(['after']);
    expect(lines).to.deep.equal([
      ['after_commit.publish_failed', { event: 'after_commit.publish_failed', errorType: 'Error', choice: 'poll_backstop' }],
    ]);
  });

  it('transactions are kept apart', async () => {
    const commits = new AfterCommitPublisher<object>();
    const [a, b] = [{}, {}];
    const seen: string[] = [];
    commits.defer(a, async () => void seen.push('a'));
    commits.defer(b, async () => void seen.push('b'));
    await commits.committed(b);
    expect(seen).to.deep.equal(['b']);
  });
});

describe('LAT1: the Redis run-ready notifier', () => {
  const provider = (publish: (channel: string, message: string) => Promise<number>, keyNamespace = '') =>
    ({ keyNamespace, getClient: () => ({ publish }) }) as unknown as IRedisConnectionProvider;

  it('publishes each notice in order on the namespaced channel Python listens on', async () => {
    const sent: Array<[string, string]> = [];
    await new RedisRunReadyNotifier(
      provider(async (channel, message) => {
        sent.push([channel, message]);
        return 1;
      }, 'tenant-a'),
    ).notify([
      { orgId: 'o', runId: 'r1', kind: 'run_ready' },
      { orgId: 'o', runId: 'r2', kind: 'run_resume' },
    ]);
    expect(runReadyChannel()).to.equal('harness:run-ready');
    expect(sent).to.deep.equal([
      ['tenant-a:harness:run-ready', '{"orgId":"o","runId":"r1","kind":"run_ready"}'],
      ['tenant-a:harness:run-ready', '{"orgId":"o","runId":"r2","kind":"run_resume"}'],
    ]);
  });

  it('never rejects: a failed publish is logged with ids only', async () => {
    const { lines, logger } = recording();
    await new RedisRunReadyNotifier(
      provider(async () => {
        throw new Error('ECONNREFUSED secret-host');
      }),
      logger,
    ).notify([{ orgId: 'o', runId: 'r1', kind: 'run_ready' }]);
    expect(lines.map(([message]) => message)).to.deep.equal(['run_ready.publish_failed']);
    expect(lines[0]?.[1]).to.include({ orgId: 'o', runId: 'r1', kind: 'run_ready', errorType: 'Error' });
    expect(JSON.stringify(lines)).to.not.contain('secret-host');
  });
});
