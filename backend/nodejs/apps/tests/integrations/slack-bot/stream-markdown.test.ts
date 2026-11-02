import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildNumberedSlackSources,
  CitationMarkers,
  splitOpenLinkTail,
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
    it('numbers each placeholder ref by first appearance and keeps a link\'s own words', () => {
      const markers = new CitationMarkers();
      expect(markers.mark('Up [source](ref4) and the [Trust Center](https://ref9.xyz/).')).to.equal(
        'Up [1] and the Trust Center [2].',
      );
      expect(markers.mark(' Again https://ref4.xyz, [ref9] and [2](ref7).')).to.equal(' Again [1], [2] and [3].');
      expect(markers.mark('A [real link](https://asana.com) and [1](/record/r1) stay.')).to.equal(
        'A [real link](https://asana.com) and [1](/record/r1) stay.',
      );
    });

    it('frees the numbers a discarded model call took', () => {
      const markers = new CitationMarkers();
      expect(markers.mark('[source](ref4)')).to.equal('[1]');
      markers.checkpoint();
      expect(markers.mark('[source](ref9)')).to.equal('[2]');
      markers.rollback();
      expect(markers.mark('[source](ref7) and [source](ref4)')).to.equal('[2] and [1]');
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
