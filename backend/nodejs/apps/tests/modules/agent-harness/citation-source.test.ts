import { expect } from 'chai';
import {
  checkedSources,
  citationSource,
  messageSources,
  sourceKey,
} from '../../../src/modules/agent-harness/projection/citation-source';
import {
  finishedConversation,
  indexed,
  live,
  liveCitation,
  recordCitation,
  storedCitation,
  webCitation,
} from './support/citations';

describe('agent-harness: where a citation came from (C-18)', () => {
  describe('citationSource', () => {
    it('a web page, by citation type or by origin', () => {
      expect(citationSource(webCitation(1))).to.deep.equal({ kind: 'web' });
      expect(citationSource({ chunkIndex: 1, citationType: 'vectordb|document', metadata: { origin: 'WEB_SEARCH', recordId: 'https://x.test' } })).to.deep.equal({ kind: 'web' });
    });

    it('a live fetch names the connector kind, the fetching instance and the item, never the temporary record', () => {
      const source = citationSource(liveCitation('file-1', 1));
      expect(source).to.deep.equal(live('file-1'));
      expect(JSON.stringify(source)).to.not.include('TEMP-');
    });

    it('a temporary record id is live even when the stored citation lost its sourceKind', () => {
      expect(citationSource(liveCitation('file-1', 1, { sourceKind: false }))).to.deep.equal(live('file-1'));
    });

    it('a live citation that does not say which item it read is unclassified, never a record', () => {
      const citation = liveCitation('file-1', 1);
      delete (citation['metadata'] as Record<string, unknown>)['externalRecordId'];
      expect(citationSource(citation)).to.deep.equal({ kind: 'unclassified' });
      const bare = liveCitation('file-2', 2, { sourceKind: false });
      delete (bare['metadata'] as Record<string, unknown>)['externalRecordId'];
      expect(citationSource(bare)).to.deep.equal({ kind: 'unclassified' });
    });

    describe('stored citations without sourceKind are classified by their metadata', () => {
      it('a connector record is indexed', () => {
        expect(citationSource(recordCitation('r1', 1))).to.deep.equal(indexed('r1'));
      });

      it('a knowledge-base upload is indexed: it has its own ACL', () => {
        expect(citationSource(storedCitation('kb-1', 1, { origin: 'UPLOAD', connector: 'KNOWLEDGE_BASE' }))).to.deep.equal(indexed('kb-1'));
      });

      it('a file uploaded into the conversation is an attachment', () => {
        expect(citationSource(storedCitation('att-1', 1, { origin: 'UPLOAD', connector: 'ATTACHMENTS' }))).to.deep.equal({
          kind: 'attachment',
          recordId: 'att-1',
        });
      });

      it('an artifact a run produced is generated', () => {
        expect(citationSource(storedCitation('art-1', 1, { recordType: 'ARTIFACT' }))).to.deep.equal({ kind: 'generated', recordId: 'art-1' });
        for (const connector of ['CODING_SANDBOX', 'IMAGE_GENERATION', 'DATABASE_SANDBOX']) {
          expect(citationSource(storedCitation('art-2', 1, { connector })), connector).to.deep.equal({ kind: 'generated', recordId: 'art-2' });
        }
      });
    });

    it('an explicit record kind is taken as it is', () => {
      for (const kind of ['indexed', 'attachment', 'generated'] as const) {
        expect(citationSource(storedCitation('r1', 1, { sourceKind: kind }))).to.deep.equal({ kind, recordId: 'r1' });
      }
    });

    it('nothing to check is unclassified', () => {
      for (const junk of ['junk', null, [], { chunkIndex: 1 }, { chunkIndex: 1, metadata: { recordId: '' } }]) {
        expect(citationSource(junk), JSON.stringify(junk)).to.deep.equal({ kind: 'unclassified' });
      }
    });
  });

  describe('checkedSources / messageSources', () => {
    it('one per record or live item, first seen first, web and unclassified left out', () => {
      const citations = [
        recordCitation('r1', 1),
        liveCitation('f1', 2),
        webCitation(3),
        storedCitation('r1', 4, { connector: 'ATTACHMENTS' }),
        liveCitation('f1', 5, { sourceKind: false }),
        { chunkIndex: 6 },
        liveCitation('f1', 7, { connector: 'ONEDRIVE' }),
      ];
      expect(checkedSources(citations)).to.deep.equal([indexed('r1'), live('f1'), live('f1', 'ONEDRIVE')]);
      expect(checkedSources(undefined)).to.deep.equal([]);
    });

    it('reads the conversation shape', () => {
      const messages = finishedConversation('s', [liveCitation('f1', 1), recordCitation('r1', 2), webCitation(3)])['messages'];
      expect(messageSources(messages)).to.deep.equal([live('f1'), indexed('r1')]);
      expect(messageSources('junk')).to.deep.equal([]);
    });

    it('a record is one key whatever its kind; a live item is keyed by connector kind and external id', () => {
      expect(sourceKey(indexed('r1'))).to.equal(sourceKey({ kind: 'attachment', recordId: 'r1' }));
      expect(sourceKey(live('f1'))).to.not.equal(sourceKey(live('f1', 'ONEDRIVE')));
      expect(sourceKey(live('f1'))).to.not.equal(sourceKey(indexed('f1')));
    });
  });
});
