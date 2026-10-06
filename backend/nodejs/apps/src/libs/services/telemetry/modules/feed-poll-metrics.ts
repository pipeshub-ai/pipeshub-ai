import { metricsBackend } from '../metrics-backend';

export type FeedPollStatus = '200' | '304';

const feedPolls = metricsBackend.createCounter({
  name: 'collab_feed_poll_total',
  help: 'Conversation feed polls answered, by HTTP status',
  labelNames: ['status'],
});

export function recordFeedPoll(status: FeedPollStatus): void {
  feedPolls.inc({ status });
}
