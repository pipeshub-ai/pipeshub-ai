import { FilterQuery, Types } from 'mongoose';
import { IChatSessionMessageDocument } from '../../../types/conversation.interfaces';

/**
 * Rows that mention `mentionId` (a user or team id) in an org. The `$type` term is not redundant: the
 * planner only picks the partial `{orgId, mentions.id, createdAt}` index when the filter states it.
 */
export const mentionedInFilter = (
  orgId: Types.ObjectId,
  mentionId: string,
): FilterQuery<IChatSessionMessageDocument> => ({
  orgId,
  'mentions.id': { $eq: mentionId, $type: 'string' },
});
