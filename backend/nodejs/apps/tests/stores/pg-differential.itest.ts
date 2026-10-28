/**
 * The same scripted use of the conversation and saved-search repositories on MongoDB and on PostgreSQL,
 * every method more than once, and the JSON-semantic comparator (key set, JSON types, array order) on every answer.
 *
 * Ids the stores make differ, so each store's log is renumbered by first appearance before the comparison; times are
 * masked (the stores stamp their own clocks). One difference is intended and normalised, nothing else is: the internal
 * `activeRunId` that two Mongo aggregate reads leak is not returned by PostgreSQL.
 *
 *   MONGO_IT_URI=mongodb://127.0.0.1:47201 REPLICA_SET_AVAILABLE=false npm run test:pg-stores
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { MongoService } from '../../src/libs/services/mongo.service';
import { jsonDiff } from '../../src/libs/repositories/json-compare';
import { MongoConversationRepository } from '../../src/libs/repositories/conversation/mongo';
import { PgConversationRepository } from '../../src/libs/repositories/conversation/pg';
import { MongoSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/mongo';
import { PgSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/pg';
import type { ConversationRepository, NewMessage, SessionListQuery } from '../../src/libs/repositories/conversation';
import type { SemanticSearchRepository } from '../../src/libs/repositories/semantic-search';
import { HEX24, OrgLedger, PG_BINS, PgStack, haveStack, newId, scoped, startPgStack } from './pg-store-harness';

interface Repos {
  conv: ConversationRepository;
  search: SemanticSearchRepository;
}
type Log = Array<{ label: string; out: unknown }>;

const ORG = newId();
const OTHER_ORG = newId();
const [ALICE, BOB, CAROL, DAVE] = [newId(), newId(), newId(), newId()];
const [PROJECT, PROJECT2] = [newId(), newId()];
const FIXED = new Set([ORG, OTHER_ORG, ALICE, BOB, CAROL, DAVE, PROJECT, PROJECT2]);
const CHAT = { kind: 'chat' } as const;
const agent = (agentKey: string) => ({ kind: 'agent', agentKey }) as const;

const meta = (orgId: string, n: number) => ({
  orgId, mimeType: 'text/plain', recordId: `rec-${n}`, recordName: `Record ${n}`, origin: 'UPLOAD', recordVersion: 1, blockNum: [n],
  bounding_box: [{ x: n, y: n + 1 }],
});
const userMsg = (content: string, extra: Partial<NewMessage> = {}): NewMessage => ({ messageType: 'user_query', content, ...extra });
const botMsg = (content: string, extra: Partial<NewMessage> = {}): NewMessage => ({ messageType: 'bot_response', content, ...extra });

/** The script. It never looks at which store it runs on; every id it uses came out of an earlier answer. */
async function script(r: Repos, step: <T>(label: string, p: Promise<T>) => Promise<T>): Promise<void> {
  const { conv, search } = r;
  const now = 1_700_000_000_000;
  const open = (over: Record<string, unknown> = {}, first = userMsg('hello world')) =>
    conv.openSession(
      { orgId: ORG, userId: ALICE, initiator: ALICE, title: 'Quarterly plan', lastActivityAt: now, status: 'Complete', sessionType: 'chat', ...over } as never,
      first,
    );
  const actor = (userId: string, orgId = ORG) => ({ orgId, userId });
  const q = (over: Partial<SessionListQuery> = {}): SessionListQuery => ({
    orgId: ORG, userId: ALICE, scope: CHAT, owned: true, shared: false, archived: 'no',
    sort: { lastActivityAt: -1, _id: -1 }, skip: 0, limit: 20, ...over,
  });

  // ---- sessions
  const s1 = await step('open s1', open({ modelInfo: { modelKey: 'k1', modelName: 'm', modelProvider: 'p', chatMode: 'standard' } }));
  const s2 = await step('open s2 in a project', open({ title: 'Roadmap', lastActivityAt: now + 10, projectId: PROJECT, projectVisibility: 'project' }, userMsg('plan the roadmap')));
  const s3 = await step('open s3 private in project', open({ title: 'Secret', lastActivityAt: now + 20, projectId: PROJECT, projectVisibility: 'private', status: 'Inprogress' }));
  const a1 = await step('open agent chat', open({ title: 'Agent talk', sessionType: 'agent', agentKey: 'ag1', conversationSource: 'agent_chat', lastActivityAt: now + 30 }));
  const a2 = await step('open agent chat 2', open({ title: 'Agent two', sessionType: 'agent', agentKey: 'ag2', conversationSource: 'agent_chat', lastActivityAt: now + 40 }));
  await step('findById', conv.findById(s1._id));
  await step('findById unknown', conv.findById(newId()));
  await step('findForTurn owner', conv.findForTurn(CHAT, s1._id, actor(ALICE)));
  await step('findForTurn stranger', conv.findForTurn(CHAT, s1._id, actor(DAVE)));
  await step('findOwned', conv.findOwned(CHAT, s1._id, actor(ALICE)));
  await step('findOwnedByInitiator', conv.findOwnedByInitiator(CHAT, s1._id, actor(ALICE)));
  await step('findForShare', conv.findForShare(s1._id, actor(ALICE)));
  await step('findForUnshare', conv.findForUnshare(s1._id, actor(BOB)));
  await step('findDeletable', conv.findDeletable(s1._id, actor(ALICE)));
  await step('findArchivable chat', conv.findArchivable(CHAT, s1._id, actor(ALICE)));
  await step('findArchivable agent', conv.findArchivable(agent('ag1'), a1._id, actor(ALICE)));
  await step('findForFeedback chat', conv.findForFeedback(CHAT, s1._id, actor(ALICE)));
  await step('findForFeedback agent', conv.findForFeedback(agent('ag1'), a1._id, actor(ALICE)));

  // ---- sharing
  await step('share s1', conv.setSharing(CHAT, s1._id, { isShared: true, sharedWith: [{ userId: BOB, accessLevel: 'write' }, { userId: CAROL, accessLevel: 'read' }] }));
  await step('share agent', conv.setSharing(agent('ag1'), a1._id, { isShared: true, sharedWith: [{ userId: BOB, accessLevel: 'read' }] }));
  await step('findForTurn writer', conv.findForTurn(CHAT, s1._id, actor(BOB)));
  await step('findForTurn reader', conv.findForTurn(CHAT, s1._id, actor(CAROL)));
  await step('findAgentWithAccess read', conv.findAgentWithAccess(a1._id, 'ag1', actor(BOB), 'read'));
  await step('findAgentWithAccess write', conv.findAgentWithAccess(a1._id, 'ag1', actor(BOB), 'write'));
  await step('findForFeedback member', conv.findForFeedback(CHAT, s1._id, actor(CAROL)));
  await step('findDeletable stranger', conv.findDeletable(s1._id, actor(CAROL)));

  // ---- lists
  await step('list owned', conv.listSessions(q()));
  await step('list owned paged', conv.listSessions(q({ skip: 1, limit: 1 })));
  await step('list owned by title asc', conv.listSessions(q({ sort: { title: 1, _id: -1 } })));
  await step('list shared with bob', conv.listSessions(q({ userId: BOB, owned: false, shared: true, omitSharedWith: true })));
  await step('list shared with bob and project', conv.listSessions(q({ userId: BOB, owned: false, shared: true, accessibleProjectIds: [PROJECT], omitSharedWith: true })));
  await step('list owned and shared', conv.listSessions(q({ userId: BOB, owned: true, shared: true, accessibleProjectIds: [PROJECT] })));
  await step('list search', conv.listSessions(q({ search: 'quarter' })));
  await step('list search with content ids', conv.listSessions(q({ search: 'zzz', contentMatchIds: [s2._id] })));
  await step('list dates', conv.listSessions(q({ startDate: new Date(Date.now() - 3600_000), endDate: new Date(Date.now() + 3600_000) })));
  await step('list shared flag', conv.listSessions(q({ isSharedFilter: true })));
  await step('list project', conv.listSessions(q({ projectFilter: { kind: 'project', id: PROJECT } })));
  await step('list unassigned', conv.listSessions(q({ projectFilter: { kind: 'unassigned' } })));
  await step('list one by id', conv.listSessions(q({ id: s1._id })));
  await step('list agent chats', conv.listSessions(q({ scope: agent('ag1'), archived: 'notTrue' })));
  await step('list agent chats shared', conv.listSessions(q({ userId: BOB, scope: agent('ag1'), archived: 'notTrue', sharedAccess: 'read' })));
  await step('list agent chats shared write', conv.listSessions(q({ userId: BOB, scope: agent('ag1'), archived: 'notTrue', sharedAccess: 'write' })));
  await step('summary', conv.findVisibleSummary(q({ id: s1._id })));
  await step('summary member', conv.findVisibleSummary(q({ id: s1._id, userId: CAROL, owned: false, shared: true })));
  await step('summary stranger', conv.findVisibleSummary(q({ id: s1._id, userId: DAVE })));
  await step('listAgentSharedWithMe', conv.listAgentSharedWithMe({ orgId: ORG, userId: BOB, agentKey: 'ag1', sort: { _id: -1 }, skip: 0, limit: 10 }));
  await step('listVisibleSummaries', conv.listVisibleSummaries(ORG, CAROL, [s1._id, s2._id, s3._id], [PROJECT]));
  await step('listByProject', conv.listByProject({ orgId: ORG, projectId: PROJECT, userId: BOB, skip: 0, limit: 10 }));
  await step('countByProject', conv.countByProject(ORG, BOB, [PROJECT, PROJECT2]));
  await step('countByProject owner', conv.countByProject(ORG, ALICE, [PROJECT, PROJECT2]));

  // ---- the run lease and the session record
  const lease = await step('reserveRun', conv.reserveRun(CHAT, s1._id, actor(ALICE)));
  await step('reserveRun busy', conv.reserveRun(CHAT, s1._id, actor(BOB), 0).catch((e: Error) => `error: ${e.message}`));
  await step('reserveRun stranger', conv.reserveRun(CHAT, s1._id, actor(DAVE), 0));
  await step('touchRun', conv.touchRun(s1._id, lease!.runId));
  await step('releaseRun', conv.releaseRun(lease!));
  await step('after release', conv.findById(s1._id));
  await step('recordRunState', conv.recordRunState(s1._id, {
    status: 'Failed', failReason: 'boom', lastActivityAt: now + 5, modelInfo: { modelKey: 'k2', modelName: undefined },
    appendError: { message: 'bad thing', errorType: 'x', stack: 's', metadata: new Map([['code', 'code-1']]) },
  }));
  await step('recordRunState clear', conv.recordRunState(s1._id, { status: 'Complete', failReason: null }));
  await step('recordRunState unknown', conv.recordRunState(newId(), { status: 'Complete' }));
  await step('setTitle', conv.setTitle(s1._id, 'Quarterly plan v2'));

  // ---- messages
  const [cit1, cit2] = await step('createCitations', conv.createCitations([
    { content: 'source one', chunkIndex: 0, citationType: 'vector', metadata: meta(ORG, 1) as never },
    { content: 'source two', chunkIndex: 1, citationType: 'vector', metadata: { ...meta(ORG, 2), connector: 'drive' } as never },
  ]));
  const [bot1] = await step('append answer by alice', conv.appendMessages(s1._id, ORG, [
    botMsg('answer [1]', {
      citations: [{ citationId: cit1!._id, relevanceScore: 0.9, excerpt: 'e', context: 'c' }, { citationId: cit2!._id }],
      confidence: 'High', followUpQuestions: [{ question: 'more?', confidence: 'Medium' }],
      modelInfo: { modelKey: 'k', modelName: 'm', modelProvider: 'p', chatMode: 'quick' } as never,
      appliedFilters: { apps: [{ id: 'a', name: 'n' }] } as never, metadata: { processingTimeMs: 5 },
      tools: [{ toolName: 't', toolResult: { rows: [1, 2] } }], reasoning: [{ content: 'thinking', turnIndex: 0 }],
      referenceData: [{ name: 'ref', id: '1' }], parts: [{ type: 'text', content: 'part' }],
    }),
  ]));
  await step('append question by bob', conv.appendMessages(s1._id, ORG, [userMsg('and what about q3?', { attachments: [{ recordId: 'att-1', recordName: 'a.pdf' }] })]));
  const [bobAnswer] = await step('append answer by bob', conv.appendMessages(s1._id, ORG, [botMsg('q3 is fine', {})]));
  await step('append a question and answer together', conv.appendMessages(s1._id, ORG, [
    userMsg('q', { attachments: [{ recordId: 'att-2' }] }), botMsg('a'),
  ]));
  await step('append status stopped', conv.appendMessages(s1._id, ORG, [botMsg('partial', { status: 'stopped', contentFormat: 'JSON' })]));
  // fields the schemas do not name are dropped on both stores; defaults are filled on both
  await step('append a message with unknown nested fields', conv.appendMessages(s1._id, ORG, [
    botMsg('rich', {
      citations: [{ citationId: cit1!._id, junk: 1 } as never], followUpQuestions: [{ question: 'q', confidence: 'Low', junk: 1 } as never],
      metadata: { processingTimeMs: 1, reason: 'dropped' } as never, modelInfo: { modelKey: 'k', junk: 1 } as never,
      attachments: [{ recordId: 'a1', recordName: 'n', junk: 1 } as never], appliedFilters: { apps: [{ id: 'a', junk: 1 }], other: [1] } as never,
      referenceData: [{ name: 'n', id: '1', metadata: { k: 'v' }, junk: 1 } as never], tools: [{ toolName: 't', toolResult: 1, junk: 1 } as never],
      reasoning: [{ content: 'r', junk: 1 } as never], parts: [{ type: 'text', content: 'x', anything: 1 } as never],
    }),
  ]));
  await step('feedback with every nested shape', conv.appendFeedback(bobAnswer!._id, ORG, {
    isHelpful: false, ratings: { accuracy: 3, junk: 1 }, categories: ['other'], comments: { positive: 'p', junk: 'x' },
    citationFeedback: [{ citationId: cit1!._id, isRelevant: true, junk: 1 }], followUpQuestionsHelpful: true, unusedFollowUpQuestions: ['u'],
    feedbackProvider: ALICE, timestamp: 5, revisions: [{ updatedFields: ['a'], previousValues: { a: 'b' }, updatedBy: BOB, updatedAt: 6, junk: 1 }],
    metrics: { timeToFeedback: 1, userAgent: 'ua', junk: 1 }, junk: 1,
  }));
  await step('citations with unknown metadata fields', conv.createCitations([
    { content: 'c', chunkIndex: 3, citationType: 'vector', metadata: { ...meta(ORG, 5), junk: 1, score: 0.5, topics: ['t'], bounding_box: [{ x: 1, y: 2, z: 3 }] } as never },
  ]));
  await step('listMessages rich', conv.listMessages(s1._id, { orgId: ORG, order: 'desc', limit: 1 }));
  await step('append empty', conv.appendMessages(s1._id, ORG, []));
  await step('append to unknown', conv.appendMessages(newId(), ORG, [userMsg('x')]).catch((e: Error) => `error: ${e.message}`));
  await step('listMessages alice', conv.listMessages(s1._id, { orgId: ORG }));
  await step('listMessages alice with citations', conv.listMessages(s1._id, { orgId: ORG, withCitations: true }));
  await step('listMessages desc page', conv.listMessages(s1._id, { orgId: ORG, order: 'desc', skip: 1, limit: 2 }));
  await step('listMessagesOfSessions', conv.listMessagesOfSessions([s1._id, s2._id, s3._id], { orgId: ORG, withCitations: true }));
  await step('countMessages', conv.countMessages(s1._id, ORG));
  await step('findMessage', conv.findMessage(s1._id, ORG, bobAnswer!._id));
  await step('findMessage wrong session', conv.findMessage(s2._id, ORG, bobAnswer!._id));
  await step('appendFeedback', conv.appendFeedback(bobAnswer!._id, ORG, { isHelpful: true, ratings: { accuracy: 4 }, comments: { positive: 'good' }, timestamp: 1 }));
  await step('appendFeedback second', conv.appendFeedback(bobAnswer!._id, ORG, { isHelpful: false, categories: ['other'], timestamp: 2, feedbackProvider: BOB }));
  await step('replaceMessage', conv.replaceMessage(bot1!._id, ORG, botMsg('regenerated', { tools: [{ toolName: 't2', toolResult: 2 }] })));
  await step('listMessages after replace', conv.listMessages(s1._id, { orgId: ORG, limit: 3 }));
  await step('listAttachmentsAndCitedRecords', conv.listAttachmentsAndCitedRecords(s1._id));
  await step('getCitations', conv.getCitations([cit1!._id, cit2!._id], ORG));
  await step('getCitations other org', conv.getCitations([cit1!._id], OTHER_ORG));
  const doomed = await step('append doomed', conv.appendMessages(s2._id, ORG, [botMsg('delete me')]));
  await step('deleteMessages', conv.deleteMessages([doomed[0]!._id], ORG));

  // ---- content search
  await step('search content', sorted(conv.findIdsByContent(ORG, 'hello')));
  await step('search content in an answer', sorted(conv.findIdsByContent(ORG, 'q3')));
  await step('search content is case blind', sorted(conv.findIdsByContent(ORG, 'QUARTER')));
  await step('search content escapes', sorted(conv.findIdsByContent(ORG, '100\\%')));
  await step('search content other org', sorted(conv.findIdsByContent(OTHER_ORG, 'hello')));
  const marked = await step('append artifact markers', conv.appendMessages(s2._id, ORG, [botMsg('see ::artifact[a1] here'), botMsg('plain'), botMsg('and ::artifact[a2]')]));
  await step('listArtifactMarkerContents', conv.listArtifactMarkerContents(s2._id, ORG, 100));
  await step('listArtifactMarkerContents up to', conv.listArtifactMarkerContents(s2._id, ORG, 2));
  await step('deleteMessages markers', conv.deleteMessages(marked.map((m) => m._id), ORG));
  const q1 = await step('append question to edit', conv.appendMessages(s3._id, ORG, [userMsg('first words', { attachments: [{ recordId: 'att-7' }] })]));
  await step('updateUserQueryContent', conv.updateUserQueryContent(q1[0]!._id, ORG, s3._id, 'edited words'));
  await step('updateUserQueryContent with files', conv.updateUserQueryContent(q1[0]!._id, ORG, s3._id, 'edited again', [{ recordId: 'att-8', recordName: 'b.pdf' }]));
  await step('updateUserQueryContent unknown', conv.updateUserQueryContent(newId(), ORG, s3._id, 'x'));

  // ---- archive, delete, project links
  await step('archive chat', conv.setArchived(CHAT, s2._id, { by: ALICE }));
  await step('archive agent', conv.setArchived(agent('ag1'), a1._id, { by: ALICE }));
  await step('archive agent 2', conv.setArchived(agent('ag2'), a2._id, { by: ALICE }));
  await step('list archived', conv.listSessions(q({ archived: 'only' })));
  await step('searchArchived', conv.searchArchived({ orgId: ORG, userId: ALICE, escapedSearch: 'a', contentMatchIds: [], deletedAgentKeys: ['ag2'], skip: 0, limit: 10 }));
  await step('searchArchived by content', conv.searchArchived({ orgId: ORG, userId: ALICE, escapedSearch: 'zzz', contentMatchIds: [s2._id], deletedAgentKeys: null, skip: 0, limit: 10 }));
  await step('listArchivedAgentGroups', conv.listArchivedAgentGroups({ orgId: ORG, userId: ALICE, deletedAgentKeys: null, agentSkip: 0, agentLimit: 10, chatsPerAgent: 3 }));
  await step('unarchive', conv.setArchived(CHAT, s2._id, null));
  await step('list after unarchive', conv.listSessions(q({ archived: 'no' })));
  // ---- pin
  const pinFirst = { isPinned: -1, lastActivityAt: -1, _id: -1 } as const;
  await step('pin s1', conv.setPinned(s1._id, actor(ALICE), true));
  await step('pin s1 again', conv.setPinned(s1._id, actor(ALICE), true));
  await step('pin by a member', conv.setPinned(s1._id, actor(BOB), true));
  await step('pin an agent chat', conv.setPinned(a1._id, actor(ALICE), true));
  await step('list pinned first', conv.listSessions(q({ sort: pinFirst })));
  await step('list shared hides the pin', conv.listSessions(q({ userId: BOB, owned: false, shared: true, omitSharedWith: true, sort: pinFirst })));
  await step('archive a pinned chat', conv.setArchived(CHAT, s1._id, { by: ALICE }));
  await step('pin an archived chat', conv.setPinned(s1._id, actor(ALICE), true));
  await step('unarchive does not bring the pin back', conv.setArchived(CHAT, s1._id, null));
  await step('list after the pin cycle', conv.listSessions(q({ sort: pinFirst })));
  await step('pin s3', conv.setPinned(s3._id, actor(ALICE), true));
  await step('unpin s3', conv.setPinned(s3._id, actor(ALICE), false));
  await step('unpin s3 again', conv.setPinned(s3._id, actor(ALICE), false));
  await step('list after unpin', conv.listSessions(q({ sort: pinFirst })));

  // ---- a copy of a conversation: a session created under an id the caller chose
  const forkId = newId();
  await step('createSession', conv.createSession({
    id: forkId, orgId: ORG, userId: ALICE, initiator: ALICE, title: 'Fork of Quarterly plan', lastActivityAt: now + 50, status: 'Complete',
    sessionType: 'chat', metadata: { forkedFromConversationId: s1._id, forkedFromMessageId: forkId },
  }));
  await step('append to the copy', conv.appendMessages(forkId, ORG, [userMsg('copied question'), botMsg('copied answer')]));
  await step('read the copy', conv.listMessages(forkId, { orgId: ORG }));
  await step('softDelete the copy', conv.softDelete(forkId, actor(ALICE)));

  await step('set project link', conv.setProjectLink(CHAT, s1._id, { projectId: PROJECT2, projectVisibility: 'private' }));
  await step('set project visibility', conv.setProjectVisibility(CHAT, s1._id, 'project'));
  await step('unlink from another project', conv.unlinkFromProject(ORG, s1._id, PROJECT));
  await step('unlink project', conv.unlinkProject(PROJECT));
  await step('after unlink', conv.listSessions(q({ projectFilter: { kind: 'unassigned' } })));
  await step('unshare', conv.setSharing(CHAT, s1._id, { isShared: false, sharedWith: [], clearShareLink: true }));
  await step('softDelete', conv.softDelete(s2._id, actor(ALICE)));
  await step('softDelete again', conv.softDelete(s2._id, actor(ALICE)).catch((e: Error) => `error: ${e.message}`));
  await step('softDeleteAgent', conv.softDeleteAgent(a2._id, ALICE));
  await step('list after deletes', conv.listSessions(q({ archived: 'any', scope: agent('ag1') })));

  // ---- saved searches
  const hit = (n: number) => ({ content: `hit ${n}`, chunkIndex: n, citationType: 'vector', metadata: meta(ORG, n) as never });
  const m1 = await step('search create', search.create({ orgId: ORG, userId: ALICE, query: 'budget', limit: 5, hits: [hit(1), hit(2)], records: new Map([['r1', '{"a":1}'], ['r2', '{"b":2}']]) }));
  const m2 = await step('search create no hits', search.create({ orgId: ORG, userId: ALICE, query: 'other', limit: 2, records: new Map() }));
  await step('search create by bob', search.create({ orgId: ORG, userId: BOB, query: 'bob search', limit: 1, hits: [hit(3)], records: new Map() }));
  await step('search findVisible', search.findVisible(ORG, ALICE, m1._id));
  await step('search findVisible populated', search.findVisible(ORG, ALICE, m1._id, { populateCitations: true }));
  await step('search findVisible stranger', search.findVisible(ORG, DAVE, m1._id));
  await step('search share', search.setSharing(m1._id, { isShared: true, shareLink: 'https://x/search', sharedWith: [{ userId: CAROL, accessLevel: 'read' }] }));
  await step('search findVisible member', search.findVisible(ORG, CAROL, m1._id));
  await step('search list', search.listVisible(ORG, ALICE, { skip: 0, limit: 10 }));
  await step('search list by created asc', search.listVisible(ORG, ALICE, { skip: 0, limit: 10, sort: { field: 'createdAt', direction: 1 } }));
  await step('search list owned only', search.listVisible(ORG, ALICE, { skip: 0, limit: 10, shared: false }));
  await step('search list shared', search.listVisible(ORG, CAROL, { skip: 0, limit: 10, owned: false, shared: true }));
  await step('search list text filters', search.listVisible(ORG, ALICE, { skip: 0, limit: 10, isShared: true, startDate: new Date(0), search: 'x' }));
  await step('search archive', search.archive(m2._id, ALICE));
  await step('search findVisible archived', search.findVisible(ORG, ALICE, m2._id, { archived: true }));
  await step('search unarchive', search.unarchive(ORG, ALICE, m2._id));
  await step('search findVisible unarchived', search.findVisible(ORG, ALICE, m2._id));
  await step('search findCitations', search.findCitations(ORG, (m1.citationIds as string[]).slice(0, 1)));
  await step('search delete', search.delete(ORG, ALICE, m2._id));
  await step('search deleteVisible', search.deleteVisible(ORG, ALICE, { shared: false }));
  await step('search list after', search.listVisible(ORG, ALICE, { skip: 0, limit: 10 }));
}

const sorted = async (p: Promise<{ ids: string[]; truncated: boolean }>) => {
  const r = await p;
  return { ...r, ids: [...r.ids].sort() };
};

const MASK_NUMBERS = new Set(['createdAt', 'updatedAt', 'decidedAt', 'timestamp', 'lastActivityAt']);

/** Renumber ids by first appearance, mask times, and apply the one documented difference. */
function normalise(log: Log): unknown[] {
  const seen = new Map<string, string>();
  const walk = (v: unknown, key?: string): unknown => {
    if (v instanceof Date) return '<time>';
    // a map keyed by ids is data (the answer of listMessagesOfSessions); any other map is the document store's Map type, whose JSON form loses its content
    if (v instanceof Map) return walk([...v.keys()].some((k) => HEX24.test(String(k))) ? [...v.entries()] : Object.fromEntries(v));
    if (key === 'runId') return '<run>';
    if (typeof v === 'string' && HEX24.test(v)) {
      if (FIXED.has(v)) return v;
      if (!seen.has(v)) seen.set(v, `#${String(seen.size + 1)}`);
      return seen.get(v);
    }
    if (typeof v === 'number' && key && MASK_NUMBERS.has(key)) return '<time>';
    if (Array.isArray(v)) return v.map((x) => walk(x));
    if (v !== null && typeof v === 'object') {
      const o = { ...(v as Record<string, unknown>) };
      delete o.activeRunId;
      return Object.fromEntries(Object.entries(o).sort(([x], [y]) => (x < y ? -1 : 1)).map(([k, x]) => [k, walk(x, k)]));
    }
    return v;
  };
  return log.map((l) => ({ label: l.label, out: walk(l.out) }));
}

/**
 * Some repository calls stamp the wall clock themselves (archiving sets the last activity). Two stores
 * running the script one after the other would stamp different milliseconds, and two chats archived in
 * consecutive steps are sometimes a tie in one store and a millisecond apart in the other, which orders
 * them differently. The clock is therefore frozen and moved by a fixed amount after every step, so both
 * stores see the same time at the same step.
 */
async function run(r: Repos): Promise<Log> {
  const log: Log = [];
  const base = 1_800_000_000_000;
  const clock = sinon.useFakeTimers({ now: base, toFake: ['Date'] });
  try {
    await script(r, async (label, p) => {
      const out = await p;
      log.push({ label, out });
      clock.setSystemTime(base + log.length * 10);
      return out;
    });
  } finally {
    clock.restore();
  }
  return log;
}

const suite = haveStack && process.env.MONGO_IT_URI ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`Mongo and PostgreSQL (${bin}) answer the same, method by method`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    let mongo: MongoService;
    const dbName = `pipeshub_diff_${randomUUID().slice(0, 8)}`;

    before(async () => {
      mongo = new MongoService({ uri: process.env.MONGO_IT_URI as string, db: dbName });
      await mongo.initialize();
      for (const model of Object.values(mongoose.models)) await model.createCollection().catch(() => undefined);
      stack = await startPgStack(bin, 'conversation_diff');
    });
    after(async () => {
      await stack?.stop();
      if (mongo?.isConnected()) {
        await mongo.getConnection().dropDatabase();
        await mongo.destroy();
      }
    });

    it('every answer is JSON-semantically equal', async () => {
      const ledger = new OrgLedger();
      const pg: Repos = {
        conv: scoped(new PgConversationRepository(), ledger, {
          findIdsByContent: 0, listArtifactMarkerContents: 1, updateUserQueryContent: 1, listVisibleSummaries: 0, appendMessages: 1, replaceMessage: 1, deleteMessages: 1, appendFeedback: 1, findMessage: 1, countMessages: 1, getCitations: 1,
        }),
        search: scoped(new PgSemanticSearchRepository(), ledger, { findVisible: 0, listVisible: 0, unarchive: 0, delete: 0, deleteVisible: 0, findCitations: 0 }),
      };
      const mongoLog = normalise(await run({ conv: new MongoConversationRepository(), search: new MongoSemanticSearchRepository() })) as Array<{ label: string; out: unknown }>;
      const pgLog = normalise(await run(pg)) as Array<{ label: string; out: unknown }>;
      expect(pgLog.map((l) => l.label)).to.deep.equal(mongoLog.map((l) => l.label));
      const problems = mongoLog.flatMap((m, i) => jsonDiff(m.out, pgLog[i]?.out).map((d) => `${m.label}: ${d}`));
      expect(problems, problems.join('\n')).to.deep.equal([]);
      // the comparison is not vacuous: nearly every step answers with something
      const empty = mongoLog.filter((m) => m.out === null || m.out === undefined || (Array.isArray(m.out) && m.out.length === 0));
      expect(mongoLog.length).to.be.greaterThan(140);
      expect(empty.map((m) => m.label)).to.have.length.below(30);
    });
  });
}
