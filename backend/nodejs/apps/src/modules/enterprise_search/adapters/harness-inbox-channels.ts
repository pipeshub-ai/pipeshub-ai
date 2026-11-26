import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import type { InboxCounts } from '../../agent-harness/domain/inbox';
import type { InboxCountPublisher } from '../../agent-harness/ports/inbox';
import type {
  SkillProposal,
  SkillProposalReader,
} from '../../agent-harness/application/inbox-sources';
import { NotificationContainer } from '../../notification/container/notification.container';

/** The socket event that carries a person's Inbox counts (`InboxCounts`). */
export const INBOX_COUNT_EVENT = 'inboxCount';

/** Pushes a person's counts to their open sessions over the app's notification socket. */
export class SocketInboxCountPublisher implements InboxCountPublisher {
  publish(userId: string, counts: InboxCounts): void {
    NotificationContainer.getNotificationService()?.sendToUser(
      userId,
      INBOX_COUNT_EVENT,
      counts,
    );
  }
}

const SKILLS_TIMEOUT_MS = 2_000;

/** The skills service's pending proposals (`GET /api/v1/skills/candidates/pending`), with the person's credential. */
export class QuerySkillProposals implements SkillProposalReader {
  constructor(private readonly aiBackend: () => string) {}

  async pending(authorization: string): Promise<SkillProposal[]> {
    const response = await new AIServiceCommand<{
      candidates?: Array<Record<string, unknown>>;
    }>({
      uri: `${this.aiBackend()}/api/v1/skills/candidates/pending`,
      method: HttpMethod.GET,
      headers: { Authorization: authorization },
      timeoutMs: SKILLS_TIMEOUT_MS,
      maxAttempts: 1,
    }).execute();
    // Skills switched off for the org answers 403: nothing waits.
    if (response.statusCode === 403 || response.statusCode === 404) return [];
    if (response.statusCode !== 200)
      throw new Error(`status ${String(response.statusCode)}`);
    return (response.data?.candidates ?? []).flatMap(
      (candidate): SkillProposal[] => {
        const candidateId = candidate['candidate_id'];
        const name = candidate['name'];
        if (
          typeof candidateId !== 'string' ||
          typeof name !== 'string' ||
          candidate['status'] !== 'pending'
        )
          return [];
        const createdAt = candidate['created_at'];
        const created =
          typeof createdAt === 'string' ? Date.parse(createdAt) : NaN;
        return [
          {
            candidateId,
            name,
            description:
              typeof candidate['description'] === 'string'
                ? candidate['description']
                : '',
            createdAt: Number.isFinite(created) ? created : 0,
          },
        ];
      },
    );
  }
}
