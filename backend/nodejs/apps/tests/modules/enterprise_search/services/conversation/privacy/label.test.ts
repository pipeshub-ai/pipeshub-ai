import { expect } from 'chai'
import { createHash } from 'crypto'
import {
  answerLabelFrom,
  archivedLabelHistory,
  labelContentHash,
  replacedLabelHistory,
  labelStateOf,
  sealAnswerForStorage,
  withoutLabelFields,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label'
import {
  LABEL_DIRECT_KEYS,
  LABEL_HISTORY_MAX,
  LABEL_ROW_FIELDS,
  LEGACY_LABEL_VER,
  type AnswerProvenance,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/label.types'
import type { IMessage } from '../../../../../../src/modules/enterprise_search/types/conversation.interfaces'

/** What the AI backend sends for Dana's answer that read record R and nothing personal. */
const danasLabel = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  v: 5,
  ver: '',
  contentHash: '',
  direct: {
    records: ['rec-R'],
    vrids: ['vrid-R'],
    nodes: ['kb-1'],
    saVrids: [],
    personal: [],
    agents: [],
    chat: [],
    projects: [],
    marks: [],
  },
  parents: [],
  complete: true,
  tagset: 3,
  pub: false,
  ...over,
})

const answerRow = (over: Partial<IMessage> = {}): IMessage => ({
  messageType: 'bot_response',
  content: 'R says revenue grew 12%.',
  contentFormat: 'MARKDOWN',
  parts: [{ type: 'text', content: 'R says revenue grew 12%.' }],
  ...over,
})

let minted = 0
const mint = (): string => `${'0'.repeat(20)}${(++minted).toString(16).padStart(4, '0')}`

describe('answer label: what the AI backend sends', () => {
  it('a complete label is kept whole, with its version and hash left for the save to fill', () => {
    const label = answerLabelFrom(danasLabel({ ver: 'python-made', contentHash: 'python-made' }))
    expect(label).to.deep.include({ v: 5, complete: true, tagset: 3, pub: false, parents: [] })
    expect(label?.direct.records).to.deep.equal(['rec-R'])
    expect(label?.direct.vrids).to.deep.equal(['vrid-R'])
    // Node, not the AI backend, says which stored version a label belongs to.
    expect(label?.ver).to.equal('')
    expect(label?.contentHash).to.equal('')
  })

  it('a file parent keeps the run that wrote the file, so a reader can be judged against that run\'s answer', () => {
    const parent = { id: '', ver: '', mode: 'full', artifactId: 'art-1', runId: 'run-wrote' }
    const label = answerLabelFrom(danasLabel({ parents: [parent] }))
    expect(label?.complete).to.equal(true)
    expect(label?.parents).to.deep.equal([parent])
  })

  it('an agent\'s entry keeps its mark of skill text the asker can\'t open, also in an archived version', () => {
    const agents = [{ key: 'agent-x', kind: 'config', skill: true }]
    const label = answerLabelFrom(danasLabel({ direct: { ...(danasLabel().direct as object), agents } }))
    expect(label?.complete).to.equal(true)
    expect(label?.direct.agents).to.deep.equal(agents)
    const sealed = sealAnswerForStorage(answerRow({ provenance: label }), mint)
    expect(labelStateOf(sealed)).to.equal('ok')
    expect(archivedLabelHistory({ ...sealed, runId: 'run-1' })?.[0]?.provenance?.direct.agents).to.deep.equal(agents)
  })

  it('an answer without a label stores none, so only its asker sees it', () => {
    expect(answerLabelFrom(undefined)).to.equal(undefined)
    expect(answerLabelFrom(null)).to.equal(undefined)
  })

  it('is a copy: changing what the AI backend sent later changes nothing stored', () => {
    const raw = danasLabel()
    const label = answerLabelFrom(raw)
    ;((raw.direct as Record<string, string[]>).records as string[]).push('rec-S')
    expect(label?.direct.records).to.deep.equal(['rec-R'])
  })

  for (const [name, raw, reason] of [
    ['an unknown version', danasLabel({ v: 6 }), 'label_unknown_version'],
    ['an unknown kind of source', danasLabel({ direct: { ...(danasLabel().direct as object), mcpServers: ['srv'] } }), 'label_unknown_version'],
    ['an unknown top-level field', danasLabel({ restrictedTo: ['u1'] }), 'label_unknown_version'],
    ['a missing kind of source', danasLabel({ direct: { records: ['rec-R'] } }), 'label_invalid'],
    ['a list that is not a list of ids', danasLabel({ direct: { ...(danasLabel().direct as object), personal: 'dana' } }), 'label_invalid'],
    ['an agent without its kind', danasLabel({ direct: { ...(danasLabel().direct as object), agents: [{ key: 'a1' }] } }), 'label_invalid'],
    ['a parent with an unknown mode', danasLabel({ parents: [{ id: 'm1', ver: 'v1', mode: 'copy' }] }), 'label_invalid'],
    ['a file parent whose writing run is not an id', danasLabel({ parents: [{ id: '', ver: '', mode: 'full', artifactId: 'art-1', runId: 7 }] }), 'label_invalid'],
    ['a completeness flag that is not true or false', danasLabel({ complete: 'yes' }), 'label_invalid'],
  ] as const) {
    it(`${name} is stored as incomplete, never as a label someone else can pass`, () => {
      const label = answerLabelFrom(raw)
      expect(label?.complete).to.equal(false)
      expect(label?.pub).to.equal(false)
      expect(label?.incomplete?.some((r) => r.startsWith(reason)), JSON.stringify(label?.incomplete)).to.equal(true)
    })
  }

  it('a label the AI backend marked incomplete keeps its reasons', () => {
    const label = answerLabelFrom(danasLabel({ complete: false, incomplete: ['untagged_prompt_part:history'] }))
    expect(label?.complete).to.equal(false)
    expect(label?.incomplete).to.deep.equal(['untagged_prompt_part:history'])
  })

  it('something that is not a label at all becomes an incomplete label, not a missing one', () => {
    for (const raw of ['{"v":5}', 42, ['rec-R']]) {
      const label = answerLabelFrom(raw)
      expect(label?.complete, String(raw)).to.equal(false)
      expect(label?.direct.records).to.deep.equal([])
    }
  })

  it('a label too large to store whole is replaced by an incomplete one, never cut', () => {
    const records = Array.from({ length: 30_000 }, (_, i) => `record-${i}-${'x'.repeat(8)}`)
    const label = answerLabelFrom(danasLabel({ direct: { ...(danasLabel().direct as object), records } }))
    expect(label?.complete).to.equal(false)
    expect(label?.incomplete).to.deep.equal(['label_over_cap'])
    expect(label?.direct.records).to.deep.equal([])
  })

  it('a label that names a record or a personal tool is never public, whatever it says', () => {
    expect(answerLabelFrom(danasLabel({ pub: true }))?.pub).to.equal(false)
    const personal = danasLabel({ pub: true, direct: { ...(danasLabel().direct as object), records: [], vrids: [], nodes: [], personal: ['dana'] } })
    expect(answerLabelFrom(personal)?.pub).to.equal(false)
    const web = danasLabel({ pub: true, direct: { ...(danasLabel().direct as object), records: [], vrids: [], nodes: [] } })
    expect(answerLabelFrom(web)?.pub).to.equal(true)
  })

  it('lists every kind of source the shared contract names', () => {
    expect([...LABEL_DIRECT_KEYS]).to.deep.equal(['records', 'vrids', 'nodes', 'saVrids', 'personal', 'agents', 'chat', 'projects', 'marks'])
  })
})

describe('answer label: the content hash', () => {
  it('is the sha256 of the stored text and steps, the same whichever order their fields come in', () => {
    const a = labelContentHash('Text', [{ type: 'text', content: 'Text', isFinal: true }])
    const b = labelContentHash('Text', [{ isFinal: true, content: 'Text', type: 'text' }])
    expect(a).to.equal(b)
    expect(a).to.match(/^[0-9a-f]{64}$/)
  })

  it('changes when the text or the steps change', () => {
    const base = labelContentHash('Text', [])
    expect(labelContentHash('Text.', [])).to.not.equal(base)
    expect(labelContentHash('Text', [{ type: 'text', content: 'x' }])).to.not.equal(base)
  })

  it('reads a NUL the same in both document stores (one keeps it, the other drops it)', () => {
    expect(labelContentHash('a\u0000b', [{ type: 'text', content: 'c\u0000d' }])).to.equal(
      labelContentHash('ab', [{ type: 'text', content: 'cd' }]),
    )
  })

  it('treats a row with no steps like a row with an empty list of them', () => {
    expect(labelContentHash('x', undefined)).to.equal(labelContentHash('x', []))
    expect(labelContentHash(undefined, undefined)).to.equal(labelContentHash('', []))
  })

  it('is not a hash of the text alone', () => {
    const textOnly = createHash('sha256').update('Text').digest('hex')
    expect(labelContentHash('Text', [])).to.not.equal(textOnly)
  })
})

describe('answer label: sealing a row for storage', () => {
  it('mints a fresh version for every answer write and binds the label to it and to the stored text', () => {
    const row = answerRow({ provenance: answerLabelFrom(danasLabel()) })
    const first = sealAnswerForStorage(row, mint)
    const second = sealAnswerForStorage(row, mint)
    expect(first.labelVer).to.match(/^[0-9a-f]{24}$/)
    expect(second.labelVer).to.not.equal(first.labelVer)
    expect(first.provenance?.ver).to.equal(first.labelVer)
    expect(first.provenance?.contentHash).to.equal(labelContentHash(first.content, first.parts))
    expect(labelStateOf(first)).to.equal('ok')
  })

  it('an error row gets a version too, and keeps its label', () => {
    const sealed = sealAnswerForStorage(answerRow({ messageType: 'error', content: 'Failed.', parts: undefined, provenance: answerLabelFrom(danasLabel()) }), mint)
    expect(sealed.labelVer).to.be.a('string')
    expect(sealed.provenance?.ver).to.equal(sealed.labelVer)
  })

  it('an answer that came without a label gets a version but no label', () => {
    const sealed = sealAnswerForStorage(answerRow(), mint)
    expect(sealed.labelVer).to.be.a('string')
    expect(sealed).to.not.have.property('provenance')
    expect(labelStateOf(sealed)).to.equal('missing')
  })

  it("leaves people's own rows alone: questions, notes and cards carry no label", () => {
    for (const messageType of ['user_query', 'tool_call', 'note'] as const) {
      const row = { messageType, content: 'Hi' } as IMessage
      expect(sealAnswerForStorage(row, mint), messageType).to.deep.equal(row)
    }
  })

  it('a copied row never keeps the version it was copied from', () => {
    const copy = sealAnswerForStorage(answerRow({ labelVer: 'from-the-source-row' }), mint)
    expect(copy.labelVer).to.not.equal('from-the-source-row')
  })

  it('does not change the row it was given', () => {
    const row = answerRow({ provenance: answerLabelFrom(danasLabel()) })
    sealAnswerForStorage(row, mint)
    expect(row).to.not.have.property('labelVer')
    expect(row.provenance?.ver).to.equal('')
  })
})

describe('answer label: archived versions', () => {
  const stored = (over: Record<string, unknown> = {}) => {
    const sealed = sealAnswerForStorage(answerRow({ provenance: answerLabelFrom(danasLabel()) }), mint)
    return { ...sealed, runId: 'run-1', ...over }
  }

  it('a regenerated answer keeps the label of the version it replaced, whole', () => {
    const old = stored()
    const history = archivedLabelHistory(old)
    expect(history).to.have.length(1)
    expect(history?.[0]).to.deep.equal({ ver: old.labelVer, runId: 'run-1', provenance: old.provenance })
  })

  // Try again redoes a failed run in its place; the failure is no version of the answer.
  it("a failed run's row that an answer replaces is no version of it: only the versions before it are kept", () => {
    const old = stored()
    expect(replacedLabelHistory(old)).to.deep.equal(archivedLabelHistory(old))
    expect(replacedLabelHistory(stored({ messageType: 'error' }))).to.equal(undefined)
    expect(replacedLabelHistory({ messageType: 'error' })).to.equal(undefined)
    const earlier = [{ ver: 'v1', runId: 'run-0' }]
    expect(replacedLabelHistory(stored({ messageType: 'error', labelHistory: earlier }))).to.deep.equal(earlier)
    // Removing a failed run's row still archives its label, with when it was retired.
    expect(archivedLabelHistory(stored({ messageType: 'error' }), new Date())).to.have.length(1)
  })

  it('a removed answer records when its version was retired', () => {
    const at = new Date('2026-11-19T10:00:00Z')
    const history = archivedLabelHistory(stored(), at)
    expect(history?.[0]?.retiredAt).to.deep.equal(at)
  })

  it('an answer written before labels is archived as the legacy version, with no label', () => {
    const history = archivedLabelHistory({ messageType: 'bot_response' })
    expect(history).to.deep.equal([{ ver: LEGACY_LABEL_VER }])
  })

  it('keeps the newest twenty, oldest first', () => {
    const older = Array.from({ length: LABEL_HISTORY_MAX }, (_, i) => ({ ver: `v${i}` }))
    const history = archivedLabelHistory(stored({ labelHistory: older }))
    expect(history).to.have.length(LABEL_HISTORY_MAX)
    expect(history?.[0]?.ver).to.equal('v1')
    expect(history?.[LABEL_HISTORY_MAX - 1]?.runId).to.equal('run-1')
  })

  it('a question or a card has no label to archive', () => {
    expect(archivedLabelHistory({ messageType: 'user_query' })).to.equal(undefined)
    expect(archivedLabelHistory({ messageType: 'tool_call' })).to.equal(undefined)
  })
})

describe('answer label: what a reader may rely on', () => {
  const sealed = () => sealAnswerForStorage(answerRow({ provenance: answerLabelFrom(danasLabel()) }), mint)

  it('a row written before labels is legacy', () => {
    expect(labelStateOf({ content: 'x' })).to.equal('legacy')
    expect(labelStateOf({ content: 'x', labelVer: LEGACY_LABEL_VER })).to.equal('legacy')
  })

  it('a row whose text was changed without a new label is stale', () => {
    expect(labelStateOf({ ...sealed(), content: 'Edited behind the label.' })).to.equal('stale')
  })

  it('a row whose version moved on without its label is stale', () => {
    expect(labelStateOf({ ...sealed(), labelVer: 'aaaaaaaaaaaaaaaaaaaaaaaa' })).to.equal('stale')
  })

  it('an incomplete label is incomplete', () => {
    const row = sealAnswerForStorage(answerRow({ provenance: answerLabelFrom(danasLabel({ complete: false, incomplete: ['no_ledger'] })) }), mint)
    expect(labelStateOf(row)).to.equal('incomplete')
  })

  it('a stored label of another version, or with an unknown kind of source, is unknown', () => {
    const row = sealed()
    const provenance = row.provenance as AnswerProvenance
    expect(labelStateOf({ ...row, provenance: { ...provenance, v: 6 } as never })).to.equal('unknown_version')
    expect(labelStateOf({ ...row, provenance: { ...provenance, direct: { ...provenance.direct, extra: [] } } as never })).to.equal('unknown_version')
  })
})

describe('answer label: never in a response', () => {
  it('drops every label field and keeps the rest of the row', () => {
    const row = { _id: 'm1', content: 'x', labelVer: 'v1', provenance: { v: 5 }, labelHistory: [{ ver: 'v0' }], consentTo: [], shareRequests: [], audience: {} }
    expect(withoutLabelFields(row)).to.deep.equal({ _id: 'm1', content: 'x', labelVer: 'v1' })
  })

  it('names the stored label fields', () => {
    expect([...LABEL_ROW_FIELDS]).to.include.members(['provenance', 'labelHistory'])
    expect([...LABEL_ROW_FIELDS]).to.not.include('labelVer')
  })

  it('returns the same object when there is nothing to drop', () => {
    const row = { _id: 'm1', content: 'x' }
    expect(withoutLabelFields(row)).to.equal(row)
  })
})
