import { expect } from 'chai';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  CitationViewer,
  SharedCitationService,
} from '../../../src/modules/agent-harness/application/shared-citations';
import { CitationAccess } from '../../../src/modules/agent-harness/projection/citation-access';
import { sourceKey } from '../../../src/modules/agent-harness/projection/citation-source';
import {
  finishedConversation,
  indexed,
  leaksOf,
  live,
  liveCitation,
  readableRecords,
  recordCitation,
  storedCitation,
  webCitation,
} from './support/citations';

type Line = [level: string, message: string, meta: Record<string, unknown>];

function recording(): { logger: HarnessLogger; lines: Line[] } {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
}

const viewer: CitationViewer = {
  authorization: 'Bearer viewer',
  orgId: 'org-1',
  sessionId: 'session-1',
  runId: 'run-1',
  requestId: 'req-1',
  surface: 'stream',
};

describe('agent-harness: shared viewers see only the citations they can read (C-6)', () => {
  describe('CitationAccess', () => {
    const open = recordCitation('rec-open', 1);
    const closed = recordCitation('rec-closed', 2);
    const web = webCitation(3);
    const access = readableRecords('rec-open');

    it('keeps a readable citation whole and a web citation as it is', () => {
      expect(access.citations([open, web])).to.deep.equal([open, web]);
    });

    it('leaves only the number of a denied citation, in its place', () => {
      const out = access.citations([open, closed, web]) as unknown[];
      expect(out).to.deep.equal([open, { chunkIndex: 2, unavailable: true }, web]);
      for (const leak of leaksOf('rec-closed')) expect(JSON.stringify(out)).to.not.include(leak);
    });

    it('hides a citation that names no record and is not a web page (nothing to check)', () => {
      expect(access.citations([{ chunkIndex: 4, metadata: {} }, 'junk'])).to.deep.equal([
        { chunkIndex: 4, unavailable: true },
        { unavailable: true },
      ]);
    });

    it('fails closed: denyRecords hides every record citation but keeps web ones', () => {
      expect(CitationAccess.denyRecords().citations([open, web])).to.deep.equal([
        { chunkIndex: 1, unavailable: true },
        web,
      ]);
    });

    it('replaces a denied conversation citation, stored citation id included', () => {
      const conversation = finishedConversation('s', [open, closed]);
      const messages = access.messages(conversation['messages']) as Array<{ citations: unknown[] }>;
      expect(messages[1]!.citations).to.deep.equal([
        { citationId: 'citation-doc-0', citationData: open },
        { redacted: true, reason: 'no_access', citationId: 'unavailable-1', citationData: { chunkIndex: 2, unavailable: true } },
      ]);
      expect(JSON.stringify(messages)).to.not.include('citation-doc-1');
      expect(messages[0]).to.deep.equal({ messageType: 'user_query', content: 'q', citations: [] });
    });

  });

  describe('CitationAccess over typed sources (C-18)', () => {
    const decided = CitationAccess.from(
      new Map([
        [sourceKey(indexed('rec-open')), 'shown'],
        [sourceKey(indexed('rec-closed')), 'denied'],
        [sourceKey(live('f-copy-open')), 'shown'],
        [sourceKey(live('f-copy-closed')), 'denied'],
        [sourceKey(live('f-no-copy')), 'unverifiable'],
      ] as const),
    );

    it('a live citation is shown whole when its indexed copy is readable', () => {
      const citation = liveCitation('f-copy-open', 1);
      expect(decided.citations([citation])).to.deep.equal([citation]);
    });

    it('a live citation is hidden when its copy is not readable, or when there is no copy', () => {
      for (const [id, reason] of [['f-copy-closed', 'denied'], ['f-no-copy', 'unverifiable']] as const) {
        const citation = liveCitation(id, 4);
        expect(decided.reason(citation), id).to.equal(reason);
        const out = decided.citations([citation]);
        expect(out, id).to.deep.equal([{ chunkIndex: 4, unavailable: true }]);
        for (const leak of [...leaksOf(id), 'TEMP-']) expect(JSON.stringify(out), leak).to.not.include(leak);
      }
    });

    it('the placeholder is the same whatever the reason: the browser is not told why', () => {
      expect(decided.citations([liveCitation('f-no-copy', 3)])).to.deep.equal(decided.citations([recordCitation('rec-closed', 3)]));
    });

    it('attachments and artifacts are decided by their record', () => {
      expect(decided.shows(storedCitation('rec-open', 1, { connector: 'ATTACHMENTS' }))).to.equal(true);
      expect(decided.shows(storedCitation('rec-closed', 1, { recordType: 'ARTIFACT' }))).to.equal(false);
    });

    it('a source nobody decided is hidden (fail closed); an unclassified one is unverifiable', () => {
      expect(decided.reason(recordCitation('never-asked', 1))).to.equal('denied');
      expect(decided.reason(liveCitation('never-asked', 1))).to.equal('denied');
      expect(decided.reason({ chunkIndex: 2, metadata: {} })).to.equal('unverifiable');
      expect(decided.reason(webCitation(3))).to.equal(undefined);
    });

    it('denyRecords hides live citations too', () => {
      expect(CitationAccess.denyRecords().shows(liveCitation('f-copy-open', 1))).to.equal(false);
    });
  });

  describe('SharedCitationService', () => {
    it('asks once, with the viewer credential, for every distinct record', async () => {
      const records = new InMemoryRecordReadAccess().grant('Bearer viewer', 'a');
      const log = recording();
      const access = await new SharedCitationService(records, log.logger).decide(viewer, [indexed('a'), indexed('b'), indexed('a')]);
      expect(records.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [indexed('a'), indexed('b')] }]);
      expect(access.shows(recordCitation('a', 1))).to.equal(true);
      expect(access.shows(recordCitation('b', 2))).to.equal(false);
      const [line] = log.lines;
      expect(line?.[0]).to.equal('info');
      expect(line?.[1]).to.equal('citations.access_checked');
      expect(line?.[2]).to.include({
        event: 'citations.access_checked',
        allowed: 1,
        denied: 1,
        records: 2,
        orgId: 'org-1',
        sessionId: 'session-1',
        runId: 'run-1',
        requestId: 'req-1',
        surface: 'stream',
      });
      expect(line?.[2]['ms']).to.be.a('number');
      expect(JSON.stringify(log.lines)).to.not.include('"b"');
    });

    it('makes no call when nothing cited a record', async () => {
      const records = new InMemoryRecordReadAccess();
      const log = recording();
      await new SharedCitationService(records, log.logger).decide(viewer, []);
      expect(records.queries).to.deep.equal([]);
      expect(log.lines).to.deep.equal([]);
    });

    for (const reason of ['refused', 'unavailable', 'invalid_response'] as const) {
      it(`fails closed and logs the reason when the check is ${reason}`, async () => {
        const records = new InMemoryRecordReadAccess().grant('Bearer viewer', 'a');
        records.failWith = reason;
        const log = recording();
        const access = await new SharedCitationService(records, log.logger).decide(viewer, [indexed('a'), live('f1')]);
        expect(access.shows(recordCitation('a', 1))).to.equal(false);
        expect(access.shows(liveCitation('f1', 2))).to.equal(false);
        expect(log.lines).to.have.length(1);
        expect(log.lines[0]?.[0]).to.equal('warn');
        expect(log.lines[0]?.[1]).to.equal('citations.access_check_failed');
        expect(log.lines[0]?.[2]).to.include({ reason, records: 2, sessionId: 'session-1', choice: 'hide_record_citations' });
      });
    }

    it('fails closed without asking when the viewer brought no credential', async () => {
      const records = new InMemoryRecordReadAccess();
      const log = recording();
      const access = await new SharedCitationService(records, log.logger).decide(
        { ...viewer, authorization: undefined },
        [indexed('a')],
      );
      expect(records.queries).to.deep.equal([]);
      expect(access.shows(recordCitation('a', 1))).to.equal(false);
      expect(log.lines[0]?.[2]).to.include({ reason: 'no_credential' });
    });

    it('fails closed when no access check is configured', async () => {
      const log = recording();
      const access = await new SharedCitationService(null, log.logger).decide(viewer, [indexed('a')]);
      expect(access.shows(recordCitation('a', 1))).to.equal(false);
      expect(log.lines[0]?.[2]).to.include({ reason: 'not_configured' });
    });

    it('messagesFor hides what the viewer cannot read in a page of messages', async () => {
      const records = new InMemoryRecordReadAccess().grant('Bearer viewer', 'rec-open');
      const conversation = finishedConversation('s', [recordCitation('rec-open', 1), recordCitation('rec-closed', 2)]);
      const messages = await new SharedCitationService(records).messagesFor(
        { ...viewer, surface: 'conversation' },
        conversation['messages'],
      );
      const body = JSON.stringify(messages);
      expect(body).to.include('NAME-rec-open');
      for (const leak of leaksOf('rec-closed')) expect(body).to.not.include(leak);
      expect(records.queries).to.have.length(1);
    });

    it('each kind, allowed and denied, in one check; the log counts per kind and per reason', async () => {
      const records = new InMemoryRecordReadAccess()
        .grant('Bearer viewer', 'rec-open', 'att-open', 'art-open', 'copy-open')
        .indexCopy('DRIVE', 'f-copy-open', 'copy-open')
        .indexCopy('DRIVE', 'f-copy-closed', 'copy-closed')
        .missing('rec-gone');
      const citations = [
        recordCitation('rec-open', 1),
        recordCitation('rec-closed', 2),
        recordCitation('rec-gone', 3),
        storedCitation('att-open', 4, { connector: 'ATTACHMENTS' }),
        storedCitation('att-closed', 5, { connector: 'ATTACHMENTS' }),
        storedCitation('art-open', 6, { recordType: 'ARTIFACT' }),
        storedCitation('art-closed', 7, { recordType: 'ARTIFACT' }),
        liveCitation('f-copy-open', 8),
        liveCitation('f-copy-closed', 9),
        liveCitation('f-no-copy', 10),
        webCitation(11),
      ];
      const log = recording();
      const conversation = finishedConversation('s', citations);
      const messages = (await new SharedCitationService(records, log.logger).messagesFor(
        { ...viewer, surface: 'conversation' },
        conversation['messages'],
      )) as Array<{ citations: Array<{ citationData: Record<string, unknown> }> }>;
      const shown = messages[1]!.citations.map((c) => c.citationData['unavailable'] !== true);
      expect(shown).to.deep.equal([true, false, false, true, false, true, false, true, false, false, true]);
      expect(records.queries).to.have.length(1);
      expect(records.queries[0]!.sources).to.have.length(10);
      const [line] = log.lines;
      expect(line?.[1]).to.equal('citations.access_checked');
      expect(line?.[2]).to.include({ allowed: 4, denied: 5, unverifiable: 1, records: 10, surface: 'conversation' });
      expect(line?.[2]).to.include({
        indexedAllowed: 1,
        indexedDenied: 2,
        indexedUnverifiable: 0,
        attachmentAllowed: 1,
        attachmentDenied: 1,
        attachmentUnverifiable: 0,
        generatedAllowed: 1,
        generatedDenied: 1,
        generatedUnverifiable: 0,
        liveAllowed: 1,
        liveDenied: 1,
        liveUnverifiable: 1,
      });
      const logged = JSON.stringify(log.lines);
      for (const hidden of ['rec-closed', 'rec-gone', 'att-closed', 'art-closed', 'f-copy-closed', 'f-no-copy', 'TEMP-']) {
        expect(logged, hidden).to.not.include(hidden);
      }
    });

    it('sentinel: the temporary record id of a live citation never reaches the check', async () => {
      const records = new InMemoryRecordReadAccess();
      const citations = [liveCitation('f1', 1), liveCitation('f2', 2, { sourceKind: false })];
      await new SharedCitationService(records).messagesFor(viewer, finishedConversation('s', citations)['messages']);
      expect(records.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [live('f1'), live('f2')] }]);
      expect(JSON.stringify(records.queries)).to.not.include('TEMP-');
    });

    it('a source the authority did not answer for is hidden', async () => {
      const records = new InMemoryRecordReadAccess().grant('Bearer viewer', 'a');
      records.dropAnswers = true;
      const access = await new SharedCitationService(records).decide(viewer, [indexed('a')]);
      expect(access.shows(recordCitation('a', 1))).to.equal(false);
    });
  });
});
