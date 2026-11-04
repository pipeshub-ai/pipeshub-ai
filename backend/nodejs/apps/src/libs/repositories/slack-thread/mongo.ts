import { connect as __connect, ConnectOptions } from 'mongoose';
import { MONGO_DB_NAME } from '../../enums/db.enum';
import { SlackThread } from './schema/slack-thread.schema';
import type { SlackThreadRepository } from './types';

/** Opens this process's connection (the Slack bot runs apart from the API). */
export const connect = async (
  url: string = process.env.MONGO_URI ?? '',
  opts: ConnectOptions = {},
): Promise<void> => {
  if (url === '') {
    throw new Error('MONGO_URI environment variable is not set.');
  }
  try {
    await __connect(url, {
      dbName: MONGO_DB_NAME,
      ...opts,
    });
    console.log('mongodb running'); // Consider using a proper logger
  } catch (err) {
    console.error('mongodb connection error:', err); // Consider using a proper logger
    throw err; // Re-throw the error to ensure the app fails to start if DB connection fails
  }
};

/**
 * Checks for and drops every index but `_id` (legacy thread/bot indexes).
 */
export const dropLegacyThreadBotIndex = async (): Promise<void> => {
  try {
    const indexes = await SlackThread.collection.getIndexes();
    for (const [indexName] of Object.entries(indexes)) {
      if (indexName === '_id_') {
        continue;
      }

      console.log(`Dropping legacy index: ${indexName}`);
      await SlackThread.collection.dropIndex(indexName);
      console.log(`Successfully dropped legacy index: ${indexName}`);
    }
  } catch {
    console.warn(
      'Error checking/dropping legacy index/the index does not exist',
    );
  }
};

export class MongoSlackThreadRepository implements SlackThreadRepository {
  async getConversationId(
    threadId: string,
    botId: string,
    email: string,
  ): Promise<string | null> {
    const record = await SlackThread.findOne({ threadId, botId, email });
    return record ? record.conversationId : null;
  }

  async saveConversationId({
    threadId,
    conversationId,
    botId,
    email,
  }: {
    threadId: string;
    conversationId: string;
    botId: string;
    email: string;
  }): Promise<void> {
    await SlackThread.updateOne(
      { threadId, botId, email },
      { $set: { conversationId } },
      { upsert: true },
    );
  }
}
