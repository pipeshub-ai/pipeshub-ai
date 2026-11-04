import { getSlackThreadRepository } from '../../../../libs/repositories/slack-thread';

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
    await getSlackThreadRepository().saveConversationId({
      threadId,
      conversationId,
      botId,
      email,
    });
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
    const conversationId = await getSlackThreadRepository().getConversationId(
      threadId,
      botId,
      email,
    );
    if (conversationId !== null) {
      return conversationId;
    } else {
      console.log(`No record found for threadId=${threadId}`);
      return null;
    }
  } catch (error) {
    console.error('Error fetching from database:', error);
    throw new Error('Failed to fetch from database');
  }
};
