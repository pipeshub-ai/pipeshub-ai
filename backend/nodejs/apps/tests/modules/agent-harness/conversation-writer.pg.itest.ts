/**
 * The durable path's conversation stores on PostgreSQL (WP-114): the chat-session authority, the conversation writer
 * and the history source, through main's conversation repository, in the caller's unit of work (Alembic head,
 * row-level security, PgBouncer in transaction mode).
 *
 * PERFE2 on PostgreSQL: the lean writer stores what the legacy chat path stores (the session, its messages, their
 * citations and the history ledger) when the legacy path runs through the same repository. Then the invariants the
 * admission and commit path rely on: a bind refuses whoever holds no such place, the session row is locked so
 * concurrent writers of one session serialise and the ledger stays exact, an aborted unit leaves nothing, and another
 * organisation sees nothing.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/agent-harness/conversation-writer.pg.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import {
  getConversationRepository,
  setConversationRepository,
  type ConversationRepository,
} from '../../../src/libs/repositories/conversation';
import { PgHarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/pg';
import { PgHarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/pg';
import { PgHarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/pg';
import { runFailureFields } from '../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import {
  appendMessages,
  buildUserQueryMessage,
  markConversationFailed,
  saveCompleteConversation,
} from '../../../src/modules/enterprise_search/utils/utils';
import { historyTurns } from '../../../src/modules/enterprise_search/utils/utils';
import {
  EMPTY_LEDGER,
  extendLedger,
  HistoryLedger,
} from '../../../src/modules/agent-harness/domain/session-import';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  RunTranscript,
  UserTurn,
} from '../../../src/modules/agent-harness/ports/conversation-writer';
import type {
  IAIModel,
  IAIResponse,
  IMessage,
  IMessagePart,
} from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import type { NewMessage } from '../../../src/libs/repositories/conversation';
import type { Tx } from '../../../src/libs/repositories/uow';
import {
  PgStack,
  hexOrg,
  pgBins,
  pgSuite,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const MODEL = {
  modelKey: 'm-1',
  modelName: 'gpt-x',
  modelProvider: 'openai',
  chatMode: 'quick',
};

const repo = (): ConversationRepository => getConversationRepository();

/** The legacy chat path's writes, through the conversation repository, with the ledger kept as the harness keeps it. */
class LegacyWriter {
  constructor(private readonly orgId: string) {}

  private async accounted<T>(
    sessionId: string,
    tx: Tx,
    write: () => Promise<T>,
  ): Promise<T> {
    const before = await repo().findHarnessSession(
      sessionId,
      this.orgId,
      { forUpdate: true },
      tx,
    );
    const result = await write();
    const ledger = before?.harnessLedger;
    if (!before || !ledger) return result;
    const lastSeq =
      (await repo().findHarnessSession(sessionId, this.orgId, {}, tx))
        ?.nextSeq ?? 0;
    if (lastSeq === ledger.seq) return result;
    let next: HistoryLedger;
    if (ledger.diverged === true || before.nextSeq !== ledger.seq) {
      next = { ...ledger, diverged: true };
    } else {
      const written = (
        await repo().listMessages(
          sessionId,
          { orgId: this.orgId, afterSeq: ledger.seq },
          tx,
        )
      ).map((row) => row.dto as unknown as IMessage & { seq: number });
      next = extendLedger(
        ledger,
        written.map((message) => ({
          seq: message.seq,
          turns: JSON.parse(JSON.stringify(historyTurns(message))) as Array<
            Record<string, unknown>
          >,
          provenance: null,
        })),
        lastSeq,
      );
    }
    await repo().setHarnessFields(
      sessionId,
      this.orgId,
      { harnessLedger: next },
      tx,
    );
    return result;
  }

  async appendUserTurn(
    sessionId: string,
    turn: UserTurn,
    tx: Tx,
  ): Promise<void> {
    const message = {
      ...buildUserQueryMessage(
        turn.query,
        undefined,
        turn.chatMode,
        turn.attachments as never,
      ),
      ...(turn.senderId !== undefined ? { senderId: turn.senderId } : {}),
    };
    await this.accounted(sessionId, tx, () =>
      appendMessages(sessionId, this.orgId, [message as NewMessage], tx),
    );
  }

  async saveRunTranscript(
    run: HarnessRun,
    transcript: RunTranscript,
    tx: Tx,
  ): Promise<boolean> {
    return this.accounted(run.sessionId, tx, async () => {
      const conversation = (await repo().findHarnessSession(
        run.sessionId,
        run.orgId,
        {},
        tx,
      ))!.session;
      if (transcript.kind === 'failure') {
        await markConversationFailed(
          conversation,
          transcript.message,
          tx,
          transcript.code,
          undefined,
          undefined,
          transcript.parts as IMessagePart[] | undefined,
          runFailureFields(run.runId, transcript),
        );
        return true;
      }
      const questions = transcript.questions.map(
        (question) =>
          ({
            messageType: 'tool_call',
            content: '',
            tools: [
              { toolName: 'ask_user_question', toolResult: question.toolData },
            ],
            createdAt: new Date(),
            updatedAt: new Date(),
          }) as unknown as NewMessage,
      );
      if (questions.length > 0)
        await appendMessages(run.sessionId, run.orgId, questions, tx);
      await saveCompleteConversation(
        conversation,
        transcript.completion as unknown as IAIResponse,
        run.orgId,
        tx,
        run.input?.model as IAIModel | undefined,
      );
      // LC2FIX: the answer this transaction wrote, an error answer included, names its run, sender and label.
      const [newest] = (
        await repo().listMessages(
          run.sessionId,
          { orgId: run.orgId, order: 'desc', limit: 1, withLabels: true },
          tx,
        )
      ).map((row) => row.dto);
      await repo().replaceMessage(
        newest!._id,
        run.orgId,
        {
          ...(newest as unknown as NewMessage),
          runId: run.runId,
          ...(run.sessionRole === 'collaborator'
            ? { senderId: run.actorId }
            : {}),
          ...(transcript.label !== undefined
            ? { label: transcript.label }
            : {}),
        } as NewMessage,
        tx,
      );
      return true;
    });
  }
}

const answer = (
  text: string,
  extra: Record<string, unknown> = {},
): RunTranscript => ({
  kind: 'answer',
  completion: {
    answer: text,
    citations: [
      {
        content: `source of ${text}`,
        chunkIndex: 2,
        citationType: 'vectordb|document',
        metadata: {
          recordId: 'r-1',
          origin: 'UPLOAD',
          recordName: 'Handbook',
          mimeType: 'application/pdf',
        },
      },
    ],
    confidence: 'High',
    followUpQuestions: [
      { question: 'And then?', confidence: 'High', reasoning: 'more' },
    ],
    parts: [{ type: 'text', content: text }],
    referenceData: [
      { name: 'PROJ-1', id: '1', app: 'jira', metadata: { key: 'PROJ-1' } },
    ],
    ...extra,
  },
  questions: [],
  label: {
    complete: true,
    sources: [{ kind: 'attachment', recordId: 'r-9' }],
  } as never,
});

const HEX = /^[0-9a-f]{24}$/;
const VOLATILE = [
  '_id',
  'lastActivityAt',
  'timestamp',
  'createdAt',
  'updatedAt',
  'version',
  '__v',
];

/** What a stored row says less what two writes of it never share (ids, clocks). */
const comparable = (value: unknown): unknown => {
  if (typeof value === 'string' && HEX.test(value)) return 'id';
  if (value instanceof Date) return 'date';
  if (Array.isArray(value)) return value.map(comparable);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !VOLATILE.includes(key))
        .map(([key, inner]) => [key, comparable(inner)]),
    );
  }
  return value;
};

for (const bin of pgBins()) {
  pgSuite(
    `agent harness conversation writer on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_conversations');
        setConversationRepository(new PgConversationRepository());
      });
      after(async () => {
        setConversationRepository();
        await stack?.stop();
      });

      const sessions = scopedStore(new PgHarnessChatSessionRepository());
      const lean = new PgHarnessConversationWriter();
      const leanReads = scopedStore(new PgHarnessConversationWriter());
      const history = new PgHarnessHistorySource();
      const conversations = scopedStore(new PgConversationRepository(), {
        findHarnessSession: 1,
      });

      let runs = 0;
      const runOf = (
        org: string,
        sessionId: string,
        actorId: string,
        collaborator = false,
      ): HarnessRun =>
        ({
          runId: `run-${(runs += 1)}`,
          orgId: org,
          sessionId,
          actorId,
          ...(collaborator ? { sessionRole: 'collaborator' } : {}),
          input: { model: MODEL },
        }) as unknown as HarnessRun;

      const newSession = async (
        org: string,
        owner: string,
        agentKey?: string,
      ): Promise<string> => {
        const sessionId = hexOrg();
        await transactAs(
          (tx) =>
            sessions.createDurableSession(
              {
                orgId: org,
                actorId: owner,
                sessionId,
                title: 'A question',
                model: MODEL as never,
                now: 1,
                ...(agentKey ? { agentKey } : {}),
              },
              tx,
            ),
          org,
        );
        return sessionId;
      };

      const storedState = async (org: string, sessionId: string) => {
        const row = await conversations.findHarnessSession(sessionId, org);
        const messages = (
          await conversations.listMessages(sessionId, {
            orgId: org,
            withLabels: true,
          })
        ).map((r) => r.dto);
        const citationIds = messages.flatMap((m) =>
          (m.citations ?? []).map((c) => String(c.citationId)),
        );
        const citations = (
          await transactAs(() => repo().getCitations(citationIds, org), org)
        ).sort((a, b) => a.content.localeCompare(b.content));
        return comparable({ row, messages, citations });
      };

      /** Each step in a unit of its own, as admission and the ending commit run them. */
      const conversation = async (
        org: string,
        owner: string,
        collaborator: string,
        write: {
          turn: (sessionId: string, turn: UserTurn, tx: Tx) => Promise<unknown>;
          transcript: (
            run: HarnessRun,
            transcript: RunTranscript,
            tx: Tx,
          ) => Promise<boolean>;
        },
      ): Promise<string> => {
        const sessionId = await newSession(org, owner);
        runs = 0;
        const step = (work: (tx: Tx) => Promise<unknown>) =>
          transactAs(work, org);
        await step((tx) =>
          write.turn(
            sessionId,
            { query: 'What is PTO?', chatMode: 'quick', attachments: [] },
            tx,
          ),
        );
        await step((tx) =>
          write.transcript(
            runOf(org, sessionId, owner),
            answer('Twenty days.'),
            tx,
          ),
        );
        await step((tx) =>
          write.turn(
            sessionId,
            {
              query: 'And sick days?',
              chatMode: 'quick',
              attachments: [],
              senderId: collaborator,
            },
            tx,
          ),
        );
        await step((tx) =>
          write.transcript(
            runOf(org, sessionId, collaborator, true),
            {
              ...answer('Ten.'),
              questions: [
                { toolData: { questions: [{ question: 'Which country?' }] } },
              ],
            } as RunTranscript,
            tx,
          ),
        );
        await step((tx) =>
          write.turn(
            sessionId,
            { query: 'Again', chatMode: 'quick', attachments: [] },
            tx,
          ),
        );
        await step((tx) =>
          write.transcript(
            runOf(org, sessionId, owner),
            {
              kind: 'failure',
              code: 'quarantined',
              message: 'It kept failing.',
              parts: [
                {
                  type: 'user_input',
                  content: 'keep me',
                  messageSeq: 1,
                  consumed: false,
                },
              ],
            } as RunTranscript,
            tx,
          ),
        );
        return sessionId;
      };

      const leanWrite = (org: string) => ({
        turn: (sessionId: string, turn: UserTurn, tx: Tx) =>
          lean.appendUserTurn({ orgId: org, sessionId }, turn, tx),
        transcript: (run: HarnessRun, transcript: RunTranscript, tx: Tx) =>
          lean.saveRunTranscript(run, transcript, tx),
      });

      it('PERFE2-6 on PostgreSQL: the same session, messages, citations and ledger as the legacy path', async () => {
        const org = hexOrg(),
          owner = hexOrg(),
          collaborator = hexOrg();
        const legacy = new LegacyWriter(org);
        const legacyWrite = {
          turn: (sessionId: string, turn: UserTurn, tx: Tx) =>
            legacy.appendUserTurn(sessionId, turn, tx),
          transcript: (run: HarnessRun, transcript: RunTranscript, tx: Tx) =>
            legacy.saveRunTranscript(run, transcript, tx),
        };
        const before = await storedState(
          org,
          await conversation(org, owner, collaborator, legacyWrite),
        );
        const after = await storedState(
          org,
          await conversation(org, owner, collaborator, leanWrite(org)),
        );
        expect(after).to.deep.equal(before);
        const ledger = (before as { row: { harnessLedger: HistoryLedger } }).row
          .harnessLedger;
        expect(
          ledger.diverged,
          'the harness wrote every message: nothing to re-import',
        ).to.equal(undefined);
        const messages = (after as { messages: Array<Record<string, unknown>> })
          .messages;
        expect(
          messages.map((m) => [
            m.messageType,
            m.runId ?? null,
            m.senderId ?? null,
          ]),
        ).to.deep.equal([
          ['user_query', null, null],
          ['bot_response', 'run-1', null],
          ['user_query', null, 'id'],
          ['tool_call', null, null],
          ['bot_response', 'run-2', 'id'],
          ['user_query', null, null],
          ['error', 'run-3', null],
        ]);
        expect(messages[6]).to.include({ errorCode: 'quarantined' });
        expect(messages[1]!.label).to.deep.equal({
          complete: true,
          sources: [{ kind: 'attachment', recordId: 'r-9' }],
        });
      });

      it('PERFE2-7 on PostgreSQL: the next import reads the history the ledger accounts for', async () => {
        const org = hexOrg(),
          owner = hexOrg();
        const sessionId = await conversation(
          org,
          owner,
          hexOrg(),
          leanWrite(org),
        );
        const read = await transactAs(
          (tx) => history.read({ orgId: org, sessionId }, tx),
          org,
        );
        expect(read.lastSeq).to.equal(7);
        expect(read.ledger).to.deep.equal(
          extendLedger({ ...EMPTY_LEDGER }, read.messages, read.lastSeq),
        );
      });

      it('the session authority: binding, stream access and placement follow the owner, the shares and the pin', async () => {
        const org = hexOrg(),
          owner = hexOrg(),
          reader = hexOrg(),
          writer = hexOrg(),
          stranger = hexOrg();
        const sessionId = await newSession(org, owner);
        const scope = (
          actorId: string,
          over: Record<string, unknown> = {},
        ) => ({ orgId: org, sessionId, actorId, ...over });
        await conversations.setSharing({ kind: 'chat' }, sessionId, {
          isShared: true,
          sharedWith: [
            { userId: reader, accessLevel: 'read' },
            { userId: writer, accessLevel: 'write' },
          ],
        });
        expect(
          await sessions.findExecutionBinding(scope(owner)),
        ).to.deep.include({ harnessVersion: 'durable-v1' });
        expect(await sessions.findExecutionBinding(scope(reader))).to.equal(
          null,
        );
        expect(
          await sessions.findExecutionBinding(scope(owner, { agentKey: 'ag' })),
        ).to.equal(null);
        expect(await sessions.findStreamAccess(scope(owner))).to.deep.equal({
          audience: 'owner',
          title: 'A question',
        });
        expect(await sessions.findStreamAccess(scope(reader))).to.deep.equal({
          audience: 'shared',
          title: 'A question',
        });
        expect(await sessions.findStreamAccess(scope(stranger))).to.equal(null);
        expect(await sessions.placement(scope(writer))).to.deep.equal({
          inProject: false,
          durable: true,
        });
        expect(await sessions.placement(scope(stranger))).to.equal(null);

        const bind = (actorId: string, as: 'owner' | 'collaborator') =>
          transactAs(
            (tx) => sessions.bindDurableSession(scope(actorId), 5, tx, as),
            org,
          );
        expect(
          await bind(reader, 'collaborator'),
          'a read share cannot write',
        ).to.equal(null);
        expect(await bind(stranger, 'collaborator')).to.equal(null);
        expect(
          await bind(writer, 'owner'),
          'a writer is not the owner',
        ).to.equal(null);
        const bound = await bind(writer, 'collaborator');
        expect(bound).to.deep.equal({
          ownerId: owner,
          shared: true,
          accounted: { lastSeq: 0, ledger: EMPTY_LEDGER },
        });
        expect(await bind(owner, 'owner')).to.deep.include({ ownerId: owner });

        // A legacy pin is never taken over; a deleted session binds nothing.
        const legacy = await newSession(org, owner);
        await transactAs(
          (tx) =>
            repo().setHarnessFields(
              legacy,
              org,
              { harnessVersion: 'legacy' },
              tx,
            ),
          org,
        );
        expect(
          await transactAs(
            (tx) =>
              sessions.bindDurableSession(
                { orgId: org, sessionId: legacy, actorId: owner },
                5,
                tx,
              ),
            org,
          ),
        ).to.equal(null);
        expect(
          await sessions.findStreamAccess({
            orgId: org,
            sessionId: legacy,
            actorId: owner,
          }),
        ).to.equal(null);
        expect(
          await sessions.placement({
            orgId: org,
            sessionId: legacy,
            actorId: owner,
          }),
        ).to.deep.equal({ inProject: false, durable: false });
        await transactAs(
          (tx) => repo().softDelete(legacy, { orgId: org, userId: owner }, tx),
          org,
        );
        expect(
          await sessions.placement({
            orgId: org,
            sessionId: legacy,
            actorId: owner,
          }),
        ).to.equal(null);
      });

      it('an agent session is found only under its agent', async () => {
        const org = hexOrg(),
          owner = hexOrg();
        const sessionId = await newSession(org, owner, 'ag-1');
        const row = await conversations.findHarnessSession(sessionId, org);
        expect(row).to.deep.include({
          sessionType: 'agent',
          harnessVersion: 'durable-v1',
        });
        expect(row!.session).to.include({
          agentKey: 'ag-1',
          conversationSource: 'agent_chat',
        });
        expect(
          await sessions.findExecutionBinding({
            orgId: org,
            sessionId,
            actorId: owner,
            agentKey: 'ag-1',
          }),
        ).to.not.equal(null);
        expect(
          await sessions.findExecutionBinding({
            orgId: org,
            sessionId,
            actorId: owner,
            agentKey: 'ag-2',
          }),
        ).to.equal(null);
        expect(
          await sessions.findExecutionBinding({
            orgId: org,
            sessionId,
            actorId: owner,
          }),
        ).to.equal(null);
      });

      it('another organisation sees nothing and writes nothing', async () => {
        const org = hexOrg(),
          other = hexOrg(),
          owner = hexOrg();
        const sessionId = await newSession(org, owner);
        const elsewhere = { orgId: other, sessionId, actorId: owner };
        expect(await sessions.findExecutionBinding(elsewhere)).to.equal(null);
        expect(await sessions.findStreamAccess(elsewhere)).to.equal(null);
        expect(
          await transactAs(
            (tx) => sessions.bindDurableSession(elsewhere, 5, tx),
            other,
          ),
        ).to.equal(null);
        expect(
          await transactAs(
            (tx) =>
              lean.saveRunTranscript(
                runOf(other, sessionId, owner),
                answer('x'),
                tx,
              ),
            other,
          ),
        ).to.equal(false);
        expect(
          await leanReads.runSessionExists(runOf(other, sessionId, owner)),
        ).to.equal(false);
        const read = await transactAs(
          (tx) => history.read({ orgId: other, sessionId }, tx),
          other,
        );
        expect(read).to.deep.equal({ messages: [], lastSeq: 0, ledger: null });
        // A unit bound to one organisation refuses a call that names another (a wiring fault, never another org's rows).
        let refused = false;
        await transactAs(
          (tx) =>
            lean.appendUserTurn(
              { orgId: org, sessionId },
              { query: 'q', chatMode: 'quick', attachments: [] },
              tx,
            ),
          other,
        ).catch(() => {
          refused = true;
        });
        expect(refused).to.equal(true);
        expect(
          ((await storedState(org, sessionId)) as { messages: unknown[] })
            .messages,
        ).to.deep.equal([]);
      });

      it('concurrent writers of one session serialise on its row: gap-free seq and an exact ledger', async () => {
        const org = hexOrg(),
          owner = hexOrg();
        const sessionId = await newSession(org, owner);
        const turns = Array.from({ length: 6 }, (_, i) => `turn ${i}`);
        await Promise.all(
          turns.map((query) =>
            transactAs(
              (tx) =>
                lean.appendUserTurn(
                  { orgId: org, sessionId },
                  { query, chatMode: 'quick', attachments: [] },
                  tx,
                ),
              org,
            ),
          ),
        );
        const read = await transactAs(
          (tx) => history.read({ orgId: org, sessionId }, tx),
          org,
        );
        expect(read.messages.map((m) => m.seq)).to.deep.equal([
          1, 2, 3, 4, 5, 6,
        ]);
        expect(read.lastSeq).to.equal(6);
        expect(read.ledger!.diverged).to.equal(undefined);
        expect(read.ledger).to.deep.equal(
          extendLedger({ ...EMPTY_LEDGER }, read.messages, 6),
        );
      });

      it('a write by someone else between harness writes marks the ledger diverged', async () => {
        const org = hexOrg(),
          owner = hexOrg();
        const sessionId = await newSession(org, owner);
        await transactAs(
          (tx) =>
            lean.appendUserTurn(
              { orgId: org, sessionId },
              { query: 'one', chatMode: 'quick', attachments: [] },
              tx,
            ),
          org,
        );
        await transactAs(
          (tx) =>
            appendMessages(
              sessionId,
              org,
              [{ messageType: 'user_query', content: 'legacy' } as NewMessage],
              tx,
            ),
          org,
        );
        await transactAs(
          (tx) =>
            lean.saveRunTranscript(
              runOf(org, sessionId, owner),
              answer('A.'),
              tx,
            ),
          org,
        );
        const row = await conversations.findHarnessSession(sessionId, org);
        expect(row!.harnessLedger).to.deep.include({ seq: 1, diverged: true });
        expect(row!.nextSeq).to.equal(3);
      });

      it('an aborted unit leaves nothing: no message, no citation, no ledger move, no status', async () => {
        const org = hexOrg(),
          owner = hexOrg();
        const sessionId = await newSession(org, owner);
        const before = await storedState(org, sessionId);
        for (const work of [
          (tx: Tx) =>
            lean.appendUserTurn(
              { orgId: org, sessionId },
              { query: 'q', chatMode: 'quick', attachments: [] },
              tx,
            ),
          (tx: Tx) =>
            lean.saveRunTranscript(
              runOf(org, sessionId, owner),
              answer('A.'),
              tx,
            ),
          (tx: Tx) =>
            lean.setConversationStatus(
              runOf(org, sessionId, owner),
              'Failed',
              9,
              tx,
            ),
          (tx: Tx) =>
            sessions.bindDurableSession(
              { orgId: org, sessionId, actorId: owner },
              9,
              tx,
            ),
        ]) {
          await transactAs(async (tx) => {
            await work(tx);
            throw new Error('abort');
          }, org).catch((error: Error) =>
            expect(error.message).to.equal('abort'),
          );
        }
        expect(await storedState(org, sessionId)).to.deep.equal(before);
      });

      it('a run writes only the durable session its actor may write', async () => {
        const org = hexOrg(),
          owner = hexOrg(),
          other = hexOrg();
        const sessionId = await newSession(org, owner);
        const save = (run: HarnessRun) =>
          transactAs(
            (tx) => lean.saveRunTranscript(run, answer('A.'), tx),
            org,
          );
        expect(
          await save(runOf(org, sessionId, other)),
          'not the owner',
        ).to.equal(false);
        expect(
          await save(runOf(org, sessionId, other, true)),
          "a collaborator's run writes the session it was bound to",
        ).to.equal(true);
        expect(
          await leanReads.runSessionExists(runOf(org, sessionId, owner)),
        ).to.equal(true);
        const agentRun = {
          ...runOf(org, sessionId, owner),
          agentKey: 'ag',
        } as HarnessRun;
        expect(await save(agentRun), 'not under another kind').to.equal(false);
        await transactAs(
          (tx) =>
            repo().softDelete(sessionId, { orgId: org, userId: owner }, tx),
          org,
        );
        expect(await save(runOf(org, sessionId, owner))).to.equal(false);
        expect(
          await leanReads.runSessionExists(runOf(org, sessionId, owner)),
        ).to.equal(false);
      });

      it('status: the owner, or a collaborator run, sets it; anyone else changes nothing', async () => {
        const org = hexOrg(),
          owner = hexOrg(),
          other = hexOrg();
        const sessionId = await newSession(org, owner);
        await transactAs(
          (tx) =>
            lean.setConversationStatus(
              runOf(org, sessionId, other),
              'Failed',
              50,
              tx,
            ),
          org,
        );
        expect(
          (await conversations.findHarnessSession(sessionId, org))!.session
            .status,
        ).to.equal('Inprogress');
        await transactAs(
          (tx) =>
            lean.setConversationStatus(
              runOf(org, sessionId, other, true),
              'Complete',
              60,
              tx,
            ),
          org,
        );
        const row = await conversations.findHarnessSession(sessionId, org);
        expect(row!.session).to.include({
          status: 'Complete',
          lastActivityAt: 60,
        });
      });

      it('regenerate: hasAnswer, and retracting the last answer removes its turn and returns the question', async () => {
        const org = hexOrg(),
          owner = hexOrg();
        const sessionId = await newSession(org, owner);
        const ref = { orgId: org, sessionId };
        const command = {
          name: 'review',
          version: '1',
          contentDigest: 'c',
          args: {},
          typed: '/review',
          owner: 'builtin',
        };
        await transactAs(
          (tx) =>
            lean.appendUserTurn(
              ref,
              { query: 'first', chatMode: 'quick', attachments: [] },
              tx,
            ),
          org,
        );
        await transactAs(
          (tx) =>
            lean.saveRunTranscript(
              runOf(org, sessionId, owner),
              answer('One.'),
              tx,
            ),
          org,
        );
        await transactAs(
          (tx) =>
            lean.appendUserTurn(
              ref,
              {
                query: 'second',
                chatMode: 'quick',
                attachments: [],
                command: command as never,
              },
              tx,
            ),
          org,
        );
        await transactAs(
          (tx) =>
            lean.saveRunTranscript(
              runOf(org, sessionId, owner),
              answer('Two.'),
              tx,
            ),
          org,
        );
        const ids = (
          await conversations.listMessages(sessionId, { orgId: org })
        ).map((r) => [r.dto._id, r.dto.messageType]);
        const [firstAnswer, lastAnswer] = ids
          .filter(([, type]) => type === 'bot_response')
          .map(([id]) => String(id));
        expect(
          await transactAs((tx) => lean.hasAnswer(ref, lastAnswer!, tx), org),
        ).to.equal(true);
        expect(
          await transactAs(
            (tx) => lean.hasAnswer(ref, String(ids[0]![0]), tx),
            org,
          ),
          'a question is no answer',
        ).to.equal(false);
        expect(
          await transactAs(
            (tx) =>
              lean.hasAnswer(
                { orgId: org, sessionId: hexOrg() },
                lastAnswer!,
                tx,
              ),
            org,
          ),
        ).to.equal(false);
        expect(
          await transactAs(
            (tx) => lean.retractLastAnswer(ref, firstAnswer!, tx),
            org,
          ),
          'not the last answer',
        ).to.equal(null);
        const question = await transactAs(
          (tx) => lean.retractLastAnswer(ref, lastAnswer!, tx),
          org,
        );
        expect(question).to.deep.include({
          query: 'second',
          chatMode: 'quick',
        });
        expect(question!.command).to.deep.include({
          name: 'review',
          typed: '/review',
        });
        const left = (
          await conversations.listMessages(sessionId, { orgId: org })
        ).map((r) => r.dto.content);
        expect(left).to.deep.equal(['first', 'One.']);
      });
    },
  );
}
