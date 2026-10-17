import 'reflect-metadata'
import { expect } from 'chai'
import {
  ArtifactCopy,
  ArtifactCopyMap,
  collectArtifactRefs,
  pointMessageAtCopies,
} from '../../../../src/modules/enterprise_search/utils/fork-artifacts'
import type { IMessage } from '../../../../src/modules/enterprise_search/types/conversation.interfaces'

const marker = (name: string, id: string, version: number | '', doc = `doc-${id}`) =>
  `::artifact[${name}](record:${id}){text/markdown|${doc}|${id}|DOCUMENT|${version}}`

const copy = (over: Partial<ArtifactCopy> = {}): ArtifactCopy => ({
  sourceArtifactId: 'old',
  sourceDocumentId: 'doc-old',
  artifactId: 'new',
  documentId: 'doc-new',
  versions: [
    { from: 1, to: 1 },
    { from: 2, to: 2 },
  ],
  ...over,
})

const message = (fields: Record<string, unknown>) => fields as unknown as IMessage

describe('collectArtifactRefs', () => {
  it('returns each artifact once with the highest version any message shows', () => {
    const refs = collectArtifactRefs([
      `Here it is\n\n${marker('a.md', 'a1', 1)}`,
      `Updated\n\n${marker('a.md', 'a1', 3)}\n\n${marker('b.png', 'b1', 1)}`,
      `Older view\n\n${marker('a.md', 'a1', 2)}`,
    ])
    expect(refs).to.deep.equal([
      { artifactId: 'a1', upToVersion: 3 },
      { artifactId: 'b1', upToVersion: 1 },
    ])
  })

  it('reads a marker without a version as version 1', () => {
    expect(collectArtifactRefs([marker('a.md', 'a1', '')])).to.deep.equal([{ artifactId: 'a1', upToVersion: 1 }])
  })

  it('ignores markers that carry no record id and text without markers', () => {
    expect(
      collectArtifactRefs(['plain answer', '::artifact[a.md](https://x/y){text/plain|doc-1||DOCUMENT|1}']),
    ).to.deep.equal([])
  })
})

describe('pointMessageAtCopies', () => {
  const copies = new ArtifactCopyMap([copy()])

  it('rewrites the card to the copy: record, document, url and version', () => {
    const out = pointMessageAtCopies(message({ content: `Done\n\n${marker('a.md', 'old', 2)}` }), copies)
    expect(out.content).to.equal(`Done\n\n${marker('a.md', 'new', 2, 'doc-new')}`)
  })

  it('maps a version through the copy, and falls back to the newest copied one', () => {
    const shifted = new ArtifactCopyMap([copy({ versions: [{ from: 1, to: 1 }, { from: 2, to: 1 }, { from: 3, to: 2 }] })])
    expect(pointMessageAtCopies(message({ content: marker('a.md', 'old', 3) }), shifted).content).to.equal(
      marker('a.md', 'new', 2, 'doc-new'),
    )
    expect(pointMessageAtCopies(message({ content: marker('a.md', 'old', 9) }), shifted).content).to.equal(
      marker('a.md', 'new', 2, 'doc-new'),
    )
  })

  it('replaces a signed url that pointed at the original document', () => {
    const signed = '::artifact[a.md](https://files.test/doc-old?sig=1){text/markdown|doc-old|old|DOCUMENT|1}'
    expect(pointMessageAtCopies(message({ content: signed }), copies).content).to.equal(
      '::artifact[a.md](https://files.test/doc-new?sig=1){text/markdown|doc-new|new|DOCUMENT|1}',
    )
  })

  it('leaves a card for an artifact that was not copied untouched', () => {
    const other = marker('b.md', 'other', 1)
    expect(pointMessageAtCopies(message({ content: other }), copies).content).to.equal(other)
  })

  it('rewrites the ids a replayed tool call shows the model, at any depth', () => {
    const parts = [
      {
        type: 'tool_call',
        args: '{"artifact_id":"old"}',
        resultPreview: 'saved old (doc-old)',
        artifactId: 'blob-1',
        parts: [{ type: 'tool_call', resultSummary: { artifact_id: 'old', version: 2 } }],
      },
    ]
    const out = pointMessageAtCopies(message({ content: 'x', parts }), copies)
    expect(out.parts).to.deep.equal([
      {
        type: 'tool_call',
        args: '{"artifact_id":"new"}',
        resultPreview: 'saved new (doc-new)',
        artifactId: 'blob-1',
        parts: [{ type: 'tool_call', resultSummary: { artifact_id: 'new', version: 2 } }],
      },
    ])
  })

  it('does not mutate the message it was given', () => {
    const parts = [{ type: 'tool_call', args: '{"artifact_id":"old"}' }]
    const input = message({ content: marker('a.md', 'old', 1), parts })
    pointMessageAtCopies(input, copies)
    expect(input.content).to.equal(marker('a.md', 'old', 1))
    expect((input.parts as unknown as { args: string }[])[0]!.args).to.equal('{"artifact_id":"old"}')
  })

  it('returns the same message when nothing was copied', () => {
    const input = message({ content: marker('a.md', 'old', 1) })
    expect(pointMessageAtCopies(input, new ArtifactCopyMap([]))).to.equal(input)
  })

  it('keeps values that are not plain data, such as dates', () => {
    const when = new Date()
    const out = pointMessageAtCopies(message({ content: 'x', parts: [{ type: 'text', at: when }] }), copies)
    expect((out.parts as unknown as { at: Date }[])[0]!.at).to.equal(when)
  })
})
