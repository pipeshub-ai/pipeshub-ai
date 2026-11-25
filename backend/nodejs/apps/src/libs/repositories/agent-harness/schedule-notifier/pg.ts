import { getConversationRepository } from '../../conversation';
import { getNotificationRepository } from '../../notification';
import { getUserRepository } from '../../user';
import { inBoundOrgUnit } from '../pg-documents/ambient';
import type { ScheduleNoticeRecords } from './records';

const ANSWER_TYPES = new Set(['bot_response', 'error']);

/**
 * `ScheduleNoticeRecords` on PostgreSQL, through main's conversation, user and notification repositories. The firer
 * and the notice sweep call with no organisation bound: each call runs as the org it is for.
 */
export const pgScheduleNoticeRecords: ScheduleNoticeRecords = {
  answer: (orgId, sessionId) =>
    inBoundOrgUnit(orgId, async () => {
      const found = await getConversationRepository().listMessages(sessionId, {
        orgId,
        order: 'desc',
        limit: 20,
      });
      const answer = found.find((m) => ANSWER_TYPES.has(m.dto.messageType));
      return typeof answer?.dto.content === 'string'
        ? answer.dto.content
        : null;
    }),

  person: (orgId, userId) =>
    inBoundOrgUnit(orgId, async () => {
      const user = await getUserRepository().getLive(orgId, userId);
      if (!user?.email) return null;
      return { email: user.email, name: user.fullName || user.email };
    }),

  saveNotice: (orgId, actorId, row) =>
    inBoundOrgUnit(orgId, async () => {
      const saved = await getNotificationRepository().insert({
        orgId,
        assignedTo: actorId,
        originService: 'AI Service',
        status: 'unread',
        isDeleted: false,
        ...row,
      });
      return { ...saved };
    }),
};
