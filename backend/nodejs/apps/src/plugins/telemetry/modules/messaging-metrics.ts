import { metricsBackend } from '../metrics-backend';

export const messagingMetrics = {
  inboxErrors: metricsBackend.createCounter({
    name: 'pipeshub_consumer_inbox_errors_total',
    help: 'Consumer inbox reads or writes that failed; the message was processed as if new (at-least-once)',
    labelNames: ['op'],
  }),
  inboxSkipped: metricsBackend.createCounter({
    name: 'pipeshub_consumer_inbox_skipped_total',
    help: 'Redelivered or replayed broker messages acknowledged without running the handler again',
    labelNames: ['consumer'],
  }),
  notificationEventRefused: metricsBackend.createCounter({
    name: 'notification_event_refused_total',
    help: 'Notification events refused before any write, by reason',
    labelNames: ['reason'],
  }),
};
