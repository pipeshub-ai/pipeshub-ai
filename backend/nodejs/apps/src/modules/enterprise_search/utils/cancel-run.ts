import { Types } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ChatSession } from '../schema/chat.session.schema';
import { RunToCancel } from '../services/collaboration/http/run-canceller';
import {
  conversationContextOf,
  conversationGrantOf,
} from '../services/collaboration/http/conversation-context';

/**
 * The run a cancel may stop. With the flag on that is the run holding the conversation's lease, and
 * only when the client named it; the browser's id is never trusted on its own. `undefined` means
 * there is nothing to cancel. A participant's cancel of another user's run reaches Python through
 * `RunCanceller` (LS-07); with the flag off the body's id is passed through unchanged.
 */
export async function runIdToCancel(
  req: AuthenticatedUserRequest,
  bodyRunId: string,
): Promise<RunToCancel | undefined> {
  if (conversationContextOf(req).collab !== true) return { runId: bodyRunId };
  const { session: granted, caller } = conversationGrantOf(req);
  const session = await ChatSession.findOne({
    _id: granted._id,
    orgId: new Types.ObjectId(caller.orgId),
    isDeleted: false,
  })
    .select('activeRun')
    .lean<{ activeRun?: { runId: string; userId: Types.ObjectId } | null }>();
  const active = session?.activeRun;
  if (active?.runId !== bodyRunId) return undefined;
  return { runId: active.runId, starterUserId: String(active.userId) };
}
