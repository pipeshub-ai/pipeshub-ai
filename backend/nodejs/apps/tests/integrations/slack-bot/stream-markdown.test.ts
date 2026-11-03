import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildNumberedSlackSources,
  citationRows,
  CitationMarkers,
  linkedCitationMarker,
  splitOpenLinkTail,
  stripNumberedCitationLinkTargets,
  withoutPlaceholderRefs,
} from '../../../src/integrations/slack-bot/src/utils/citations';
import {
  parseArtifactMarkers,
  stripArtifactMarkers,
} from '../../../src/integrations/slack-bot/src/utils/parse-artifact-markers';

const cite = (n: number, name: string, webUrl: string) => ({
  citationData: { chunkIndex: n, metadata: { recordId: `r${String(n)}`, recordName: name, webUrl } },
});

describe('slack-bot streamed markdown', () => {
  describe('CitationMarkers', () => {
    const row = (name: string) => ({ name, url: `https://x.test/${name}` });

    it('numbers each placeholder ref by first appearance and keeps a link\'s own words', () => {
      const markers = new CitationMarkers();
      markers.checkpoint();
      const rows = new Map([[1, row('a')], [2, row('b')], [3, row('c')]]);
      const show = (text: string) => markers.resolve(markers.mark(text), rows, false)[0];
      expect(show('Up [source](ref4) and the [Trust Center](https://ref9.xyz/).')).to.equal(
        'Up [[1]](https://x.test/a) and the Trust Center [[2]](https://x.test/b).',
      );
      expect(show(' Again https://ref4.xyz, [ref9] and [2](ref7).')).to.equal(
        ' Again [[1]](https://x.test/a), [[2]](https://x.test/b) and [[3]](https://x.test/c).',
      );
      expect(show('A [real link](https://asana.com) and [1](/record/r1) stay.')).to.equal(
        'A [real link](https://asana.com) and [1](/record/r1) stay.',
      );
    });

    it('holds the text from a marker whose link is not known yet, and never sends a broken one', () => {
      const markers = new CitationMarkers();
      markers.checkpoint();
      const marked = markers.mark('Revenue grew [source](ref4). Next part.');
      expect(markers.resolve(marked, new Map(), false)).to.deep.equal(['Revenue grew ', marked.slice('Revenue grew '.length)]);
      expect(markers.resolve(marked, new Map([[1, row('q3')]]), false)).to.deep.equal([
        'Revenue grew [[1]](https://x.test/q3). Next part.', '',
      ]);
      // A marker whose link never came is left out, so every number shown is in the Sources list.
      expect(markers.resolve(markers.mark('Late [source](ref8).'), new Map(), true)[0]).to.equal('Late.');
      expect([...markers.sources().keys()]).to.deep.equal([1]);
    });

    it('links each call\'s markers by their place in that call, keeping one number per ref', () => {
      const markers = new CitationMarkers();
      markers.checkpoint();
      markers.resolve(markers.mark('[source](ref4)'), new Map([[1, row('first')]]), false);
      markers.checkpoint();
      // The second call numbers its citations afresh: ref9 is its first, ref4 its second.
      expect(markers.resolve(markers.mark('[source](ref9) and [source](ref4)'), new Map([[1, row('second')], [2, row('first')]]), false)[0])
        .to.equal('[[2]](https://x.test/second) and [[1]](https://x.test/first)');
    });

    it('frees the numbers a discarded model call took', () => {
      const markers = new CitationMarkers();
      const rows = new Map([[1, row('a')], [2, row('b')]]);
      markers.checkpoint();
      markers.resolve(markers.mark('[source](ref4)'), rows, false);
      markers.checkpoint();
      markers.resolve(markers.mark('[source](ref9)'), rows, false);
      markers.rollback();
      expect(markers.resolve(markers.mark('[source](ref7) and [source](ref4)'), rows, false)[0]).to.equal(
        '[[2]](https://x.test/a) and [[1]](https://x.test/a)',
      );
      expect([...markers.sources().keys()]).to.deep.equal([1, 2]);
    });

    it('spaces adjacent markers', () => {
      const markers = new CitationMarkers();
      markers.checkpoint();
      expect(markers.resolve(markers.mark('Up [source](ref1)[source](ref2).'), new Map([[1, row('a')], [2, row('b')]]), false)[0])
        .to.equal('Up [[1]](https://x.test/a) [[2]](https://x.test/b).');
    });
  });

  describe('stripNumberedCitationLinkTargets', () => {
    it('turns only the citations\' numbered links into clickable [n] to the source, leaving other numbered links', () => {
      expect(
        stripNumberedCitationLinkTargets(
          'Up [1](/record/r1/preview#blockIndex=2)[1](/record/r1), see [7](https://example.com/list).',
          [cite(1, 'Q3', 'https://x.test/q3')],
          'https://app.test',
        ),
      ).to.equal('Up [[1]](https://x.test/q3) [[1]](https://x.test/q3), see [7](https://example.com/list).');
      expect(stripNumberedCitationLinkTargets('No [1](https://a.test)', [], 'https://app.test')).to.equal('No [1](https://a.test)');
    });
  });

  describe('citationRows and linkedCitationMarker', () => {
    it('makes every link absolute and encodes parentheses so the link stays whole', () => {
      const rows = citationRows([cite(1, 'Q3', 'https://x.test/q3'), cite(2, 'Rel', '/record/r2')], 'https://app.test/');
      expect([...rows.values()].map((r) => r.url)).to.deep.equal(['https://x.test/q3', 'https://app.test/record/r2']);
      expect(linkedCitationMarker(3, 'https://x.test/a_(b)')).to.equal('[[3]](https://x.test/a_%28b%29)');
    });
  });

  describe('withoutPlaceholderRefs', () => {
    it('keeps the words of a named placeholder link and drops bare citations', () => {
      expect(withoutPlaceholderRefs('See the [Trust Center](https://ref12.xyz), [source](ref3) and https://ref5.xyz/ now.'))
        .to.equal('See the Trust Center,  and  now.');
    });
  });

  describe('splitOpenLinkTail', () => {
    it('holds a link or a bare URL that is still being written', () => {
      expect(splitOpenLinkTail('Up 12% [source](re')).to.deep.equal(['Up 12% ', '[source](re']);
      expect(splitOpenLinkTail('Up 12% [1]')).to.deep.equal(['Up 12% ', '[1]']);
      expect(splitOpenLinkTail('See https://ref1')).to.deep.equal(['See ', 'https://ref1']);
      expect(splitOpenLinkTail('See ht')).to.deep.equal(['See ', 'ht']);
      expect(splitOpenLinkTail('Up 12% [1](/record/r1) done')).to.deep.equal(['Up 12% [1](/record/r1) done', '']);
      expect(splitOpenLinkTail('with')[1]).to.equal('');
    });

    it('holds an artifact marker until its closing brace', () => {
      for (const tail of ['::art', '::artifact[report.pdf](rec', '::artifact[report.pdf](record:abc)', '::artifact[r](x){application/pdf|']) {
        expect(splitOpenLinkTail(`Ready. ${tail}`)).to.deep.equal(['Ready. ', tail]);
      }
      expect(splitOpenLinkTail('Ready. ::artifact[r](x){m} Done.')[1]).to.equal('');
    });
  });

  describe('stripArtifactMarkers', () => {
    it('removes markers but keeps the whitespace that joins streamed pieces', () => {
      expect(stripArtifactMarkers('Here it is ::artifact[r.pdf](record:a){application/pdf||a} \n')).to.equal('Here it is  \n');
      expect(stripArtifactMarkers('word ')).to.equal('word ');
      expect(parseArtifactMarkers('word ').text).to.equal('word');
    });
  });

  describe('buildNumberedSlackSources', () => {
    it('lists sources by citation number so the [n] markers match', () => {
      const blocks = buildNumberedSlackSources(
        [cite(3, 'Notes', 'https://x.test/n'), cite(1, 'Q3 report', 'https://x.test/q3'), cite(3, 'Dup', 'https://x.test/d')],
        'https://app.test',
      );

      expect(blocks[0]).to.deep.equal({ type: 'section', text: { type: 'mrkdwn', text: '*Sources:*' } });
      const elements = (blocks[1] as { elements: { elements: { text: string }[] }[] }).elements[0]!.elements;
      expect(elements.map((e) => e.text).join('')).to.equal('[1] Q3 report\n[3] Notes\n');
    });
  });
});
