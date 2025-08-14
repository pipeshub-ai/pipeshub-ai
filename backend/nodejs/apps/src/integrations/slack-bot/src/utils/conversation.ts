import { Conversation } from './db';

interface SaveToDatabaseParams {
  threadId: string;
  conversationId: string;
  email: string;
}

export const saveToDatabase = async ({
  threadId,
  conversationId,
  email,
}: SaveToDatabaseParams): Promise<void> => {
  try {
    await Conversation.updateOne(
      { threadId, email },
      { $set: { conversationId } },
      { upsert: true },
    );

    console.log(
      `Saved to DB: threadId=${threadId}, conversationId=${conversationId}`,
    );
  } catch (error) {
    console.error('Error saving to database:', error);
    throw new Error('Failed to save to database');
  }
};

export const getFromDatabase = async (
  threadId: string,
  email: string,
): Promise<string | null> => {
  try {
    const record = await Conversation.findOne({ threadId, email });
    if (record) {
      console.log(
        `Fetched from DB: threadId=${threadId}, conversationId=${record.conversationId}`,
      );

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
