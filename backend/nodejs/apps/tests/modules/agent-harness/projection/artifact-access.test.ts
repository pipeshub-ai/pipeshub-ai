import { expect } from 'chai';
import {
  ArtifactAccess,
  cardArtifactId,
  markerArtifactIds,
  messageArtifactIds,
  UNAVAILABLE_ARTIFACT_MARKER,
} from '../../../../src/modules/agent-harness/projection/artifact-access';

const marker = (name: string, recordId: string, url = `record:${recordId}`) =>
  `::artifact[${name}](${url}){text/csv|doc-${recordId}|${recordId}|SPREADSHEET|2}`;

describe('agent-harness projection: ArtifactAccess (SS0)', () => {
  const access = ArtifactAccess.showing(['art-open']);

  it('AN-1: a shown card is unchanged; a hidden or undecided one is a placeholder naming nothing', () => {
    const open = { artifactId: 'art-open', fileName: 'open.csv', recordId: 'art-open' };
    expect(access.card(open)).to.equal(open);
    expect(access.card({ artifactId: 'art-secret', fileName: 'secret.csv' })).to.deep.equal({ unavailable: true });
    expect(access.card({ recordId: 'art-open' })).to.deep.equal({ recordId: 'art-open' });
    expect(access.card({ fileName: 'no-id.csv' })).to.deep.equal({ unavailable: true });
    expect(access.card('junk')).to.deep.equal({ unavailable: true });
    expect(ArtifactAccess.hideAll().card(open)).to.deep.equal({ unavailable: true });
  });

  it('AN-2: hidden markers become the placeholder; markers without lineage are always hidden', () => {
    const text = [
      'Answer.',
      marker('open.csv', 'art-open'),
      marker('secret.csv', 'art-secret'),
      '::artifact[no-meta.csv](https://bucket.example/x.csv)',
      '::artifact[bare.csv]',
      '::artifact[no-url.csv]{text/csv|doc|art-open|SPREADSHEET|1}',
      '::artifact[old.csv](https://bucket.example/old.csv?sig=1){text/csv||||}',
      '::download_conversation_task[export.csv](https://bucket.example/export.csv?X-Amz-Signature=1)',
    ].join('\n');
    const out = access.text(text);
    expect(out.split('\n')).to.deep.equal([
      'Answer.',
      marker('open.csv', 'art-open'),
      UNAVAILABLE_ARTIFACT_MARKER,
      UNAVAILABLE_ARTIFACT_MARKER,
      UNAVAILABLE_ARTIFACT_MARKER,
      '::artifact[no-url.csv]{text/csv|doc|art-open|SPREADSHEET|1}',
      UNAVAILABLE_ARTIFACT_MARKER,
      UNAVAILABLE_ARTIFACT_MARKER,
    ]);
    for (const leak of ['secret.csv', 'art-secret', 'bucket.example', 'export.csv']) {
      expect(out, leak).to.not.include(leak);
    }
    expect(access.text('no markers here')).to.equal('no markers here');
  });

  it('a kept marker is not matched again by a shorter form', () => {
    const kept = marker('open.csv', 'art-open');
    expect(access.text(`${kept} and ${kept}`)).to.equal(`${kept} and ${kept}`);
  });

  it('messages: answer text, nested text parts and tool parts referencing a hidden artifact', () => {
    const messages = [
      { messageType: 'user_query', content: 'q' },
      {
        messageType: 'bot_response',
        content: marker('secret.csv', 'art-secret'),
        parts: [
          { type: 'tool_call', artifactId: 'art-secret-result', toolName: 'run_code' },
          { type: 'tool_call', artifactId: 'art-open' },
          { type: 'sub_agent', parts: [{ type: 'text', content: marker('secret.csv', 'art-secret') }] },
        ],
      },
    ];
    expect(messageArtifactIds(messages)).to.deep.equal(['art-secret', 'art-secret-result', 'art-open', 'art-secret']);
    const out = access.messages(messages);
    expect(out[0]).to.deep.equal(messages[0]);
    expect(JSON.stringify(out)).to.not.include('art-secret');
    expect(out[1]!.parts![1]).to.deep.equal({ type: 'tool_call', artifactId: 'art-open' });
    expect(out[1]!.content).to.equal(UNAVAILABLE_ARTIFACT_MARKER);
  });

  it('completion: the answer and its final parts', () => {
    const completion = { answer: marker('secret.csv', 'art-secret'), parts: [{ type: 'text', content: marker('open.csv', 'art-open') }] };
    expect(access.completion(completion)).to.deep.equal({
      answer: UNAVAILABLE_ARTIFACT_MARKER,
      parts: [{ type: 'text', content: marker('open.csv', 'art-open') }],
    });
  });

  it('ids: from markers with meta, and from cards by artifactId then recordId', () => {
    expect(markerArtifactIds(`${marker('a', 'a1')} ::artifact[b]{m|d|b1|t|1} ::artifact[c](x)`)).to.deep.equal(['a1', 'b1']);
    expect(markerArtifactIds(undefined)).to.deep.equal([]);
    expect(cardArtifactId({ artifactId: 'x', recordId: 'y' })).to.equal('x');
    expect(cardArtifactId({ recordId: 'y' })).to.equal('y');
    expect(cardArtifactId(null)).to.equal(undefined);
  });

  it('decisions made in batches add up', () => {
    const both = ArtifactAccess.showing(['a']).with(ArtifactAccess.showing(['b']));
    expect(both.shows('a') && both.shows('b')).to.equal(true);
    expect(both.shows('c')).to.equal(false);
    expect(both.shows(undefined)).to.equal(false);
  });
});
