import { Types } from 'mongoose';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { getMessages } from '../../../../src/modules/enterprise_search/utils/utils';

/** A session's messages, read in the session's own org as every reader must since #4492. */
export const sessionMessages = async (
  sessionId: string | Types.ObjectId,
  options: Omit<Parameters<typeof getMessages>[1], 'orgId'> = {},
): Promise<any[]> => {
  const session = await ChatSession.findById(sessionId, { orgId: 1 }).lean();
  if (session === null) return [];
  return getMessages(sessionId, { ...options, orgId: session.orgId });
};
