import 'reflect-metadata';
import { expect } from 'chai';
import {
  buildNumberedSlackSources,
  plainCitationMarkers,
  splitOpenLinkTail,
} from '../../../src/integrations/slack-bot/src/utils/citations';
import {
  parseArtifactMarkers,
  stripArtifactMarkers,
} from '../../../src/integrations/slack-bot/src/utils/parse-artifact-markers';

describe('slack-bot streamed markdown', () => {
  describe('plainCitationMarkers', () => {
    it('turns record and tiny-ref citations into plain markers and keeps web links', () => {
      expect(plainCitationMarkers('Up [1](/record/r1/preview#blockIndex=2) and [2](ref2), see [3](https://x.test).'))
        .to.equal('Up [1] and [2], see [3](https://x.test).');
      expect(plainCitationMarkers('Named [source](ref4) link.')).to.equal('Named  link.');
    });
  });

  describe('splitOpenLinkTail', () => {
    it('holds a link that is still being written', () => {
      expect(splitOpenLinkTail('Up 12% [1](/record/r')).to.deep.equal(['Up 12% ', '[1](/record/r']);
      expect(splitOpenLinkTail('Up 12% [1]')).to.deep.equal(['Up 12% ', '[1]']);
      expect(splitOpenLinkTail('Up 12% [1](/record/r1) done')).to.deep.equal(['Up 12% [1](/record/r1) done', '']);
    });

    it('holds an artifact marker until its closing brace', () => {
      for (const tail of ['::art', '::artifact[report.pdf](rec', '::artifact[report.pdf](record:abc)', '::artifact[r](x){application/pdf|']) {
        expect(splitOpenLinkTail(`Ready. ${tail}`)).to.deep.equal(['Ready. ', tail]);
      }
      expect(splitOpenLinkTail('Ready. ::artifact[r](x){m} Done.')[1]).to.equal('');
      expect(splitOpenLinkTail('As follows:')).to.deep.equal(['As follows', ':']);
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
    it('lists sources by citation number so the [N] markers match', () => {
      const cite = (n: number, name: string, webUrl: string) => ({
        citationData: { chunkIndex: n, metadata: { recordId: `r${String(n)}`, recordName: name, webUrl } },
      });
      const blocks = buildNumberedSlackSources(
        [cite(3, 'Notes', 'https://x.test/n'), cite(1, 'Q3 report', 'https://x.test/q3'), cite(3, 'Dup', 'https://x.test/d')],
        'https://app.test',
      );

      expect(blocks[0]).to.deep.equal({ type: 'section', text: { type: 'mrkdwn', text: '*Sources:*' } });
      const elements = (blocks[1] as { elements: { elements: { text: string }[] }[] }).elements[0]!.elements;
      expect(elements.map((e) => e.text).join('')).to.equal('[1] Q3 report\n[3] Notes\n');
    });

    it('is empty when no citation carries a number', () => {
      expect(buildNumberedSlackSources([{ citationData: { metadata: { recordId: 'r' } } }], 'https://app.test')).to.deep.equal([]);
    });
  });
});
