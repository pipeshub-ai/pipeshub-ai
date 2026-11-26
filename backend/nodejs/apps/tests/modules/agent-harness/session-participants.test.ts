import { expect } from 'chai';
import {
  participantChanges,
  sessionAppend,
  sessionForkedEvent,
  sessionParticipantChangedEvent,
} from '../../../src/modules/agent-harness/domain/session-participants';
import { SessionParticipants } from '../../../src/modules/agent-harness/domain/session-roles';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { JournalRejectedError } from '../../../src/modules/agent-harness/domain/errors';
import { JOURNAL_EVENT_WRITERS } from '../../../src/modules/agent-harness/contracts/event-catalog';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalStore } from '../../../src/modules/agent-harness/ports/journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { testCipher } from './support/data-protection';
import { describeSessionParticipantRecorderContract } from './support/session-participant-recorder-contract';

const OWNER = 'aa0000000000000000000001';
const ANN = 'aa0000000000000000000002';
const BOB = 'aa0000000000000000000003';

const session = (
  isShared: boolean,
  shares: Array<[string, string]>,
): SessionParticipants => ({
  ownerId: OWNER,
  isShared,
  shares: shares.map(([userId, accessLevel]) => ({ userId, accessLevel })),
});

describe('SS1 participant changes (PE-1)', () => {
  const unshared = session(false, []);
  const cases: Array<[string, SessionParticipants, SessionParticipants, unknown[]]> = [
    ['a new read share', unshared, session(true, [[ANN, 'read']]), [{ subjectId: ANN, role: 'viewer', prevRole: 'none' }]],
    ['a new write share', unshared, session(true, [[ANN, 'write']]), [{ subjectId: ANN, role: 'collaborator', prevRole: 'none' }]],
    ['the same level again', session(true, [[ANN, 'read']]), session(true, [[ANN, 'read']]), []],
    ['read to write', session(true, [[ANN, 'read']]), session(true, [[ANN, 'write']]), [{ subjectId: ANN, role: 'collaborator', prevRole: 'viewer' }]],
    ['an unshare', session(true, [[ANN, 'read'], [BOB, 'write']]), session(true, [[ANN, 'read']]), [{ subjectId: BOB, role: 'none', prevRole: 'collaborator' }]],
    ['the last share turned off', session(true, [[ANN, 'read']]), session(false, [[ANN, 'read']]), [{ subjectId: ANN, role: 'none', prevRole: 'viewer' }]],
    ['the owner listed as a share', unshared, session(true, [[OWNER, 'write']]), []],
    ['an unknown level', unshared, session(true, [[ANN, 'comment']]), [{ subjectId: ANN, role: 'viewer', prevRole: 'none' }]],
    ['two at once, sorted by user', unshared, session(true, [[BOB, 'read'], [ANN, 'write']]), [
      { subjectId: ANN, role: 'collaborator', prevRole: 'none' },
      { subjectId: BOB, role: 'viewer', prevRole: 'none' },
    ]],
  ];
  for (const [name, before, after, expected] of cases) {
    it(name, () => {
      expect(participantChanges(before, after)).to.deep.equal(expected);
    });
  }
});

describe('SS1 session events (PE-2)', () => {
  const changed = sessionParticipantChangedEvent(OWNER, { subjectId: ANN, role: 'viewer', prevRole: 'none' });
  const forked = sessionForkedEvent({ actorId: BOB, fromSessionId: 'ses-0', atSeq: 41 });

  it('records principals, not names, on the session stream, shared and non-critical', () => {
    expect(changed).to.deep.equal({
      type: 'session.participant_changed',
      schemaVersion: 1,
      critical: false,
      visibility: 'shared',
      data: { by: `p_${OWNER}`, subject: `p_${ANN}`, role: 'viewer', prevRole: 'none' },
    });
    expect(forked.data).to.deep.equal({ by: `p_${BOB}`, fromSessionId: 'ses-0', atSeq: 41 });
    expect(sessionAppend('org-1', 'ses-1', [changed])).to.deep.equal({
      orgId: 'org-1',
      sessionId: 'ses-1',
      streamId: 'ses-1',
      writer: 'node',
      events: [changed],
    });
  });

  it('Node writes them; a worker may not', () => {
    expect(() => validateJournalAppend(sessionAppend('org-1', 'ses-1', [changed, forked]))).to.not.throw();
    expect(() =>
      validateJournalAppend({ ...sessionAppend('org-1', 'ses-1', [changed]), writer: 'worker', runId: 'run-1' }),
    ).to.throw(JournalRejectedError);
    expect(JOURNAL_EVENT_WRITERS['session.participant_changed']).to.deep.equal(['node']);
    expect(JOURNAL_EVENT_WRITERS['session.forked']).to.deep.equal(['node']);
  });
});

describeSessionParticipantRecorderContract('in-memory journal', async () => {
  const audit = new InMemoryAuditChainStore();
  const journal = new AuditedJournalStore(new SealedJournalStore(new InMemoryJournalStore(), testCipher()), audit);
  return {
    recorder: new SessionParticipantJournal(journal, new JournalService(new InMemoryPayloadStore(), testCipher())),
    journal,
    audit,
    transact: (work) => work(undefined),
  };
});

describe('SessionParticipantJournal logging and failures (PE-L)', () => {
  const setup = (inner: JournalStore<unknown> = new InMemoryJournalStore()) => {
    const lines: Array<[string, string, Record<string, unknown>]> = [];
    const logger: HarnessLogger = {
      debug: () => undefined,
      info: (message, meta) => void lines.push(['info', message, meta ?? {}]),
      warn: (message, meta) => void lines.push(['warn', message, meta ?? {}]),
      error: (message, meta) => void lines.push(['error', message, meta ?? {}]),
    };
    const journal = new AuditedJournalStore(inner, new InMemoryAuditChainStore());
    const service = new JournalService(new InMemoryPayloadStore(), testCipher());
    return { lines, recorder: new SessionParticipantJournal(journal, service, logger) };
  };
  const change = (before: SessionParticipants, after: SessionParticipants) => ({
    orgId: 'org-1',
    sessionId: 'ses-1',
    actorId: OWNER,
    before,
    after,
    requestId: 'req-1',
  });

  it('PE-L: logs one info line per journaled change set, with ids and counts only', async () => {
    const { lines, recorder } = setup();
    await recorder.participantsChanged(
      change(session(true, [[ANN, 'read']]), session(true, [[ANN, 'write'], [BOB, 'read']])),
      undefined,
    );
    expect(lines).to.have.length(1);
    expect(lines[0]![0]).to.equal('info');
    expect(lines[0]![1]).to.equal('session.participants_journaled');
    expect(lines[0]![2]).to.deep.include({
      event: 'session.participants_journaled',
      orgId: 'org-1',
      sessionId: 'ses-1',
      actorId: OWNER,
      requestId: 'req-1',
      changes: 2,
      firstSeq: 1,
      lastSeq: 2,
    });
    expect(JSON.stringify(lines)).to.not.include(ANN);
  });

  it('logs nothing when no role changed', async () => {
    const { lines, recorder } = setup();
    const same = session(true, [[ANN, 'read']]);
    expect(await recorder.participantsChanged(change(same, same), undefined)).to.equal(0);
    expect(lines).to.deep.equal([]);
  });

  it('a journal failure propagates (the caller aborts its transaction), logged once at warn', async () => {
    const failing = new InMemoryJournalStore();
    failing.append = async () => {
      throw new Error('write conflict');
    };
    const { lines, recorder } = setup(failing);
    let caught: unknown;
    try {
      await recorder.participantsChanged(change(session(false, []), session(true, [[ANN, 'read']])), undefined);
    } catch (error) {
      caught = error;
    }
    expect((caught as Error).message).to.equal('write conflict');
    expect(lines.map(([level, message]) => [level, message])).to.deep.equal([
      ['warn', 'session.participants_journal_failed'],
    ]);
    expect(lines[0]![2]).to.deep.include({ kind: 'session.participants_journaled', errorType: 'Error', changes: 1 });
  });
});
