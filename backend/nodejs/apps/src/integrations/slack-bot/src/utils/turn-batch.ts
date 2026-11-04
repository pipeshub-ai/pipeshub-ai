import type { SlackFile, SlackMessagePayload } from '../helpers';
import type { QueuedSlackMessage } from './thread-queue';

export function batchText(texts: string[]): string {
  return texts.map((t) => t.trim()).filter((t) => t !== '').join('\n\n');
}

/** One message standing for the batch: the newest message's ids, everyone's text and files. */
export function mergeBatchMessage(batch: SlackMessagePayload[]): SlackMessagePayload {
  const byId = new Map<string, SlackFile>();
  const noId: SlackFile[] = [];
  for (const message of batch) {
    for (const file of (message.files ?? []) as SlackFile[]) {
      if (file.id === undefined) noId.push(file);
      else if (!byId.has(file.id)) byId.set(file.id, file);
    }
  }
  return {
    ...batch[batch.length - 1]!,
    text: batchText(batch.map((m) => m.text ?? '')),
    files: [...byId.values(), ...noId],
  };
}

/** The batch's own messages are the question, so they stay out of the prior-thread context. */
export function withoutBatchMessages(
  prior: SlackMessagePayload[],
  batchTs: ReadonlySet<string>,
): SlackMessagePayload[] {
  return prior.filter((m) => !batchTs.has(m.ts));
}

/** Only the fields the bot reads; Slack's raw file objects carry previews and content that must not sit in Redis. */
function toFileMetadata(raw: unknown): SlackFile {
  const f = raw as SlackFile;
  const out: SlackFile = { id: f.id };
  for (const key of ['name', 'mimetype', 'filetype', 'size', 'url_private_download', 'url_private'] as const) {
    if (f[key] !== undefined) (out as unknown as Record<string, unknown>)[key] = f[key];
  }
  return out;
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
    files: (message.files ?? []).map(toFileMetadata),
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
