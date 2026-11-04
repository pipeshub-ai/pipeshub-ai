import type { SlackFile, SlackMessagePayload } from '../helpers';
import type { QueuedSlackMessage } from './thread-queue';

export function batchText(texts: string[]): string {
  return texts.map((t) => t.trim()).filter((t) => t !== '').join('\n\n');
}

/** One message standing for the batch: the newest message's ids, everyone's text and files. */
export function mergeBatchMessage(batch: SlackMessagePayload[]): SlackMessagePayload {
  const files = new Map<string, unknown>();
  for (const message of batch) {
    for (const file of message.files ?? []) {
      const id = (file as SlackFile).id;
      if (!files.has(id)) files.set(id, file);
    }
  }
  return {
    ...batch[batch.length - 1]!,
    text: batchText(batch.map((m) => m.text ?? '')),
    files: [...files.values()],
  };
}

/** The batch's own messages are the question, so they stay out of the prior-thread context. */
export function withoutBatchMessages(
  prior: SlackMessagePayload[],
  batchTs: ReadonlySet<string>,
): SlackMessagePayload[] {
  return prior.filter((m) => !batchTs.has(m.ts));
}

export function toQueuedMessage(
  kind: 'dm' | 'mention',
  message: SlackMessagePayload,
  now: () => number = Date.now,
): QueuedSlackMessage {
  return {
    kind,
    ts: message.ts,
    threadTs: message.thread_ts ?? message.ts,
    channel: message.channel ?? '',
    user: message.user ?? '',
    text: message.text ?? '',
    files: (message.files ?? []) as SlackFile[],
    receivedAt: now(),
  };
}

export function fromQueuedMessage(item: QueuedSlackMessage): SlackMessagePayload {
  return {
    ts: item.ts,
    thread_ts: item.threadTs,
    channel: item.channel,
    user: item.user,
    text: item.text,
    files: item.files,
  };
}
