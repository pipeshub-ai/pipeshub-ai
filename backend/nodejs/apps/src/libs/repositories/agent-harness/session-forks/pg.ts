import { getConversationRepository, type NewMessage } from '../../conversation';
import type { Tx } from '../../uow';
import {
  type ForkKey,
  forkRecordId,
  type ForkResult,
} from '../../../../modules/agent-harness/domain/session-forks';
import { EMPTY_LEDGER } from '../../../../modules/agent-harness/domain/session-import';
import type { SessionExecutionScope } from '../../../../modules/agent-harness/ports/chat-session-repository';
import {
  ForkAlreadyCommittedError,
  type ForkCommit,
  type ForkRecord,
  type ForkSource,
  type SessionForkStore,
} from '../../../../modules/agent-harness/ports/session-forks';
import { decodeDoc, docParam, orgUnit, rowRegion } from '../pg-documents/pg';
import { hasValidIds, sessionIs } from '../chat-session-repository/pg';
import { sessionParticipantsOf } from '../session-roles/mongo';
import { sourceMessage } from './mongo';

interface ForkRow {
  fork_session_id: string;
  result: unknown;
}

const recordOf = (row: ForkRow): ForkRecord => ({
  forkSessionId: row.fork_session_id,
  result: row.result === null ? null : decodeDoc<ForkResult>(row.result),
});

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/**
 * "Continue privately" (SS3) on PostgreSQL: the request records in `harness.session_forks`, the conversations through
 * the conversation repository. `commit` marks the record done under a `result IS NULL` guard and writes the new
 * conversation (owned by the forker, not shared, no project), its messages and new citation rows, all in `tx`.
 */
export class PgHarnessSessionForkStore implements SessionForkStore<Tx> {
  find(key: ForkKey): Promise<ForkRecord | null> {
    return orgUnit(undefined, key.orgId, { write: false }, async (k) => {
      const { rows } = await k.query<ForkRow>(
        `/* pipeshub:harness.session_fork_find */ SELECT fork_session_id, result FROM harness.session_forks
          WHERE org_id = $1 AND id = $2`,
        [key.orgId, forkRecordId(key)],
      );
      return rows[0] ? recordOf(rows[0]) : null;
    });
  }

  begin(key: ForkKey, newSessionId: string, now: number): Promise<ForkRecord> {
    return orgUnit(undefined, key.orgId, { write: true }, async (k) => {
      // The first ask chooses the fork's session id; a repeat (or a racing ask) reads the one chosen.
      await k.query(
        `/* pipeshub:harness.session_fork_begin */ INSERT INTO harness.session_forks
          (org_id, id, source_session_id, actor_id, fork_session_id, result, created_at, completed_at, region)
          VALUES ($1, $2, $3, $4, $5, NULL, $6, NULL, $7) ON CONFLICT (org_id, id) DO NOTHING`,
        [
          key.orgId,
          forkRecordId(key),
          key.sourceSessionId,
          key.actorId,
          newSessionId,
          now,
          rowRegion(),
        ],
      );
      const { rows } = await k.query<ForkRow>(
        `/* pipeshub:harness.session_fork_find */ SELECT fork_session_id, result FROM harness.session_forks
          WHERE org_id = $1 AND id = $2`,
        [key.orgId, forkRecordId(key)],
      );
      return recordOf(rows[0] as ForkRow);
    });
  }

  async source(scope: SessionExecutionScope): Promise<ForkSource | null> {
    if (!hasValidIds(scope.sessionId, scope.orgId, scope.actorId)) return null;
    return orgUnit(undefined, scope.orgId, { write: false }, async () => {
      const repo = getConversationRepository();
      const row = await repo.findHarnessSession(scope.sessionId, scope.orgId);
      if (
        row === null ||
        !sessionIs.live(row) ||
        !sessionIs.ofKind(row, scope.agentKey)
      ) {
        return null;
      }
      const isAgent = row.sessionType === 'agent';
      const messages = await repo.listMessages(scope.sessionId, {
        orgId: scope.orgId,
        withCitations: true,
        withLabels: true,
      });
      return {
        participants: sessionParticipantsOf(row.session),
        sessionType: isAgent ? 'agent' : 'chat',
        agentKey: isAgent ? (row.session.agentKey ?? null) : null,
        title: row.session.title ?? '',
        modelInfo: isObject(row.session.modelInfo)
          ? row.session.modelInfo
          : null,
        durable: sessionIs.durable(row),
        messages: messages.map((message) =>
          sourceMessage(message.dto as unknown as Record<string, unknown>),
        ),
      };
    });
  }

  async commit(fork: ForkCommit, tx: Tx): Promise<void> {
    const orgId = fork.key.orgId;
    await orgUnit(tx, orgId, { write: true }, async (k) => {
      const marked = await k.query(
        `/* pipeshub:harness.session_fork_commit */ UPDATE harness.session_forks SET result = $3::jsonb, completed_at = $4
          WHERE org_id = $1 AND id = $2 AND result IS NULL`,
        [orgId, forkRecordId(fork.key), docParam(fork.result), fork.now],
      );
      if ((marked.rowCount ?? 0) === 0) throw new ForkAlreadyCommittedError();
    });
    const repo = getConversationRepository();
    const owner = fork.key.actorId;
    const isAgent = fork.source.sessionType === 'agent';
    await repo.createSession(
      {
        id: fork.forkSessionId,
        orgId,
        userId: owner,
        initiator: owner,
        title: fork.source.title,
        lastActivityAt: fork.now,
        status: 'Complete',
        ...(isAgent
          ? {
              sessionType: 'agent' as const,
              ...(fork.source.agentKey !== null
                ? { agentKey: fork.source.agentKey }
                : {}),
              conversationSource: 'agent_chat' as const,
            }
          : { sessionType: 'chat' as const }),
        ...(fork.source.modelInfo !== null
          ? { modelInfo: fork.source.modelInfo }
          : {}),
        // An empty ledger: the next durable admission imports the copied messages (session-import.ts).
        ...(fork.source.durable
          ? {
              harnessVersion: 'durable-v1' as const,
              harnessLedger: EMPTY_LEDGER,
            }
          : {}),
      },
      tx,
    );
    const created = await repo.createCitations(
      fork.messages.flatMap((message) =>
        message.citations.map((citation) => ({
          ...citation.data,
          metadata: { ...citation.data.metadata },
        })),
      ),
      tx,
    );
    let next = 0;
    const citationIdAt = (): string => {
      const citation = created[next++];
      if (citation === undefined)
        throw new Error('A copied citation was not written');
      return citation._id;
    };
    await repo.appendMessages(
      fork.forkSessionId,
      orgId,
      fork.messages.map(
        (message) =>
          ({
            messageType: message.messageType,
            content: message.content,
            ...message.fields,
            citations: message.citations.map(({ data: _data, ...use }) => ({
              ...use,
              citationId: citationIdAt(),
            })),
            // Copied turns name no run: the original's runs are read only through the original (FORKFIX).
            ...(message.foreignTurn ? { foreignTurn: true } : { copied: true }),
          }) as unknown as NewMessage,
      ),
      tx,
    );
  }
}
