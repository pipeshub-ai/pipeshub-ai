/**
 * What every `SessionParticipantRecorder` must do (SS1 PE-4, PE-9/FK-7,
 * PE-10): journal who may read a session on its root stream, audit-chained,
 * in the caller's transaction. `SessionParticipantJournal` over the in-memory
 * journal (`session-participants.test.ts`) and over the Mongo journal
 * (`session-participants.mongo.itest.ts`), both sealed and audited as in
 * production.
 */
import { expect } from 'chai';
import { verifyAuditChain } from '../../../../src/modules/agent-harness/domain/audit';
import { SessionParticipants } from '../../../../src/modules/agent-harness/domain/session-roles';
import { AuditChainStore } from '../../../../src/modules/agent-harness/ports/audit';
import { JournalStore } from '../../../../src/modules/agent-harness/ports/journal-store';
import { SessionParticipantRecorder } from '../../../../src/modules/agent-harness/ports/session-participants';

export interface ParticipantRecorderBackend<Tx> {
  recorder: SessionParticipantRecorder<Tx>;
  journal: JournalStore<Tx>;
  audit: AuditChainStore<Tx>;
  transact<T>(work: (tx: Tx) => Promise<T>): Promise<T>;
}

const OWNER = 'aa0000000000000000000001';
const ANN = 'aa0000000000000000000002';
const BOB = 'aa0000000000000000000003';

const session = (isShared: boolean, shares: Array<[string, string]>): SessionParticipants => ({
  ownerId: OWNER,
  isShared,
  shares: shares.map(([userId, accessLevel]) => ({ userId, accessLevel })),
});

const change = (
  before: SessionParticipants,
  after: SessionParticipants,
  overrides: { orgId?: string; sessionId?: string } = {},
) => ({
  orgId: 'org-1',
  sessionId: 'ses-1',
  actorId: OWNER,
  before,
  after,
  requestId: 'req-1',
  ...overrides,
});

export function describeSessionParticipantRecorderContract<Tx>(
  name: string,
  create: () => Promise<ParticipantRecorderBackend<Tx>>,
): void {
  describe(`SessionParticipantRecorder contract: ${name}`, () => {
    let b: ParticipantRecorderBackend<Tx>;

    beforeEach(async () => {
      b = await create();
    });

    const record = (...args: Parameters<typeof change>) =>
      b.transact((tx) => b.recorder.participantsChanged(change(...args), tx));
    const stream = (sessionId: string, orgId = 'org-1') => b.journal.readStream(orgId, sessionId, 0, 100);

    it('SP-1: one event per changed role on the session root stream, in order, no run, principals only', async () => {
      const count = await record(session(true, [[ANN, 'read']]), session(true, [[ANN, 'write'], [BOB, 'read']]));
      expect(count).to.equal(2);
      const events = await stream('ses-1');
      expect(events.map((e) => [e.seq, e.type, e.streamId, e.runId, e.writer, e.visibility, e.critical, e.data])).to.deep.equal([
        [1, 'session.participant_changed', 'ses-1', undefined, 'node', 'shared', false, { by: `p_${OWNER}`, subject: `p_${ANN}`, role: 'collaborator', prevRole: 'viewer' }],
        [2, 'session.participant_changed', 'ses-1', undefined, 'node', 'shared', false, { by: `p_${OWNER}`, subject: `p_${BOB}`, role: 'viewer', prevRole: 'none' }],
      ]);
    });

    it('SP-2: every event is chained in the org audit stream with its journal seq and ids', async () => {
      await record(session(false, []), session(true, [[ANN, 'read']]));
      const events = await stream('ses-1');
      const records = await b.audit.read('org-1', 0, 10);
      expect(records.map((r) => [r.kind, r.sessionId, r.runId, r.journalSeq, r.fields])).to.deep.equal(
        events.map((e) => ['session.participant_changed', 'ses-1', null, e.seq, e.data]),
      );
    });

    it('SP-3: a change that moves no role journals nothing', async () => {
      const same = session(true, [[ANN, 'read']]);
      expect(await record(same, same)).to.equal(0);
      expect(await record(session(false, []), session(true, [[OWNER, 'write']]))).to.equal(0);
      expect(await stream('ses-1')).to.deep.equal([]);
      expect(await b.audit.head('org-1')).to.equal(null);
    });

    it('SP-4: share, re-share and unshare continue one stream in order', async () => {
      await record(session(false, []), session(true, [[ANN, 'read']]));
      await record(session(true, [[ANN, 'read']]), session(true, [[ANN, 'write']]));
      await record(session(true, [[ANN, 'write']]), session(false, []));
      const events = await stream('ses-1');
      expect(events.map((e) => [e.seq, (e.data as { role: string }).role])).to.deep.equal([
        [1, 'viewer'],
        [2, 'collaborator'],
        [3, 'none'],
      ]);
    });

    it("SP-5: the same session id in another org is another stream and another chain", async () => {
      await record(session(false, []), session(true, [[ANN, 'read']]));
      await record(session(false, []), session(true, [[BOB, 'write']]), { orgId: 'org-2' });
      expect((await stream('ses-1')).map((e) => (e.data as { subject: string }).subject)).to.deep.equal([`p_${ANN}`]);
      expect((await stream('ses-1', 'org-2')).map((e) => [e.seq, (e.data as { subject: string }).subject])).to.deep.equal([
        [1, `p_${BOB}`],
      ]);
      expect((await b.audit.read('org-2', 0, 10)).map((r) => r.seq)).to.deep.equal([1]);
    });

    it('SP-6: a fork is the first event of the new session and an audit event on the source', async () => {
      await b.transact((tx) => b.recorder.forked('org-1', 'ses-2', { actorId: BOB, fromSessionId: 'ses-1', atSeq: 7 }, tx));
      const [event, ...more] = await stream('ses-2');
      expect(more).to.deep.equal([]);
      expect(event).to.deep.include({ seq: 1, type: 'session.forked', streamId: 'ses-2', writer: 'node', visibility: 'shared' });
      expect(event!.data).to.deep.equal({ by: `p_${BOB}`, fromSessionId: 'ses-1', atSeq: 7 });
      const [source] = await stream('ses-1');
      expect(source).to.deep.include({ type: 'session.forked', streamId: 'ses-1', visibility: 'audit' });
      expect(source!.data).to.deep.equal({ by: `p_${BOB}`, fromSessionId: 'ses-1', atSeq: 7, intoSessionId: 'ses-2' });
      expect((await b.audit.read('org-1', 0, 10)).map((r) => r.fields)).to.deep.equal([
        { by: `p_${BOB}`, fromSessionId: 'ses-1', atSeq: 7, intoSessionId: 'ses-2' },
        { by: `p_${BOB}`, fromSessionId: 'ses-1', atSeq: 7 },
      ]);
    });

    it('SP-7: concurrent changes to one session are all journaled, gap-free, on one verified chain', async () => {
      const users = ['aa0000000000000000000011', 'aa0000000000000000000012', 'aa0000000000000000000013', 'aa0000000000000000000014'];
      const counts = await Promise.all(users.map((user) => record(session(false, []), session(true, [[user, 'read']]))));
      expect(counts).to.deep.equal([1, 1, 1, 1]);
      const events = await stream('ses-1');
      expect(events.map((e) => e.seq)).to.deep.equal([1, 2, 3, 4]);
      expect(events.map((e) => (e.data as { subject: string }).subject).sort()).to.deep.equal(users.map((u) => `p_${u}`));
      const records = await b.audit.read('org-1', 0, 10);
      expect(verifyAuditChain(records, { head: await b.audit.head('org-1') })).to.deep.include({ ok: true, records: 4 });
    });
  });
}
