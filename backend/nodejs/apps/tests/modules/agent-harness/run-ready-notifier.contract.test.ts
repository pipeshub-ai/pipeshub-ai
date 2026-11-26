import { InMemoryRunReadyNotifier } from '../../../src/modules/agent-harness/adapters/in-memory-run-ready';
import { describeRunReadyNotifierContract } from './support/run-ready-notifier-contract';
import { collected } from './support/collected';

describe('RunReadyNotifier in memory', () => {
  describeRunReadyNotifierContract('InMemoryRunReadyNotifier', async () => {
    const notifier = new InMemoryRunReadyNotifier();
    const sink = collected<unknown>();
    notifier.listen((notice) => sink.push(notice));
    return { notifier, received: sink.first };
  });
});
