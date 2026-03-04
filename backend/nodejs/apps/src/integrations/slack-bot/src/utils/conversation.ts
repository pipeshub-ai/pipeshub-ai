import { Conversation } from './db';

interface SaveToDatabaseParams {
  threadId: string;
  conversationId: string;
  botId: string;
  email: string;
}

export const saveToDatabase = async ({
  threadId,
  conversationId,
  botId,
  email,
}: SaveToDatabaseParams): Promise<void> => {
  try {
    await Conversation.updateOne(
      { threadId, botId, email },
      { $set: { conversationId } },
      { upsert: true },
    );
  } catch (error) {
    console.error('Error saving to database:', error);
    throw new Error('Failed to save to database');
  }
};

export const getFromDatabase = async (
  threadId: string,
  botId: string,
  email: string,
): Promise<string | null> => {
  try {
    const record = await Conversation.findOne({ threadId, botId, email });
    if (record) {
      return record.conversationId;
    } else {
      console.log(`No record found for threadId=${threadId}`);
      return null;
    }
  } catch (error) {
    console.error('Error fetching from database:', error);
    throw new Error('Failed to fetch from database');
  }
};
