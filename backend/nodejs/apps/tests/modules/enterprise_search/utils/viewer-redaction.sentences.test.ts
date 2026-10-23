import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { formatPreviousConversations } from '../../../../src/modules/enterprise_search/utils/utils'
import {
  redactAnswerText,
  redactConversationFor,
  redactMessagesFor,
  redactMessagesForViewer,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'

/** Where the model writes its citation relative to the sentence's full stop. */
const SECRET = 'Secret merger codename is Bluefin'
const URL1 = 'http://h/record/r1/preview#blockIndex=0'
const URL2 = 'http://h/record/r2/preview#blockIndex=0'

const cite = (n: number, recordId: string) => ({
  citationId: {
    _id: `cit-${n}`,
    chunkIndex: n,
    content: `chunk ${recordId}`,
    metadata: { recordId, recordName: `name-${recordId}`, origin: 'UPLOAD', webUrl: `https://app/${recordId}` },
  },
})
/** Stored answers carry the canonical link form; bare markers in the table below become it. */
const lk = (t: string) => t.replace(/\[{1,2}(\d+)\]{1,2}(?!\()/g, (_m, n) => `[${n}](https://app/r${n})`)
const message = (content: string) => ({
  _id: 'm1',
  messageType: 'bot_response',
  content: lk(content),
  citations: [cite(1, 'r1'), cite(2, 'r2'), cite(3, 'r3')],
})

interface Form {
  name: string
  text: string
  /** Text that must be gone for a viewer who can read only r2. */
  hidden: string[]
  /** Text that must stay. */
  kept?: string[]
}

const FORMS: Form[] = [
  { name: 'marker before the stop', text: `${SECRET} [1]. Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'marker after the stop, spaced', text: `${SECRET}. [1] Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'marker glued after the stop', text: `${SECRET}.[1] Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'marker after the stop with its own stop', text: `${SECRET}. [1]. Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'glued marker with its own stop', text: `${SECRET}.[1]. Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'markdown link before the stop', text: `${SECRET} [1](${URL1}). Open [2](${URL2}).`, hidden: ['Bluefin', URL1], kept: [URL2] },
  { name: 'markdown link after the stop', text: `${SECRET}. [1](${URL1}). Open [2](${URL2}).`, hidden: ['Bluefin', URL1], kept: [URL2] },
  { name: 'glued markdown link after the stop', text: `${SECRET}.[1](${URL1}) Open [2](${URL2}).`, hidden: ['Bluefin', URL1], kept: [URL2] },
  { name: 'several unreadable markers', text: `${SECRET}. [1][3]. Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'double brackets', text: `${SECRET}. [[1]] Open fact [[2]].`, hidden: ['Bluefin'], kept: ['Open fact [[2]]'] },
  { name: 'abbreviation inside the sentence', text: `${SECRET}, e.g. the Bluefin deal. [1] Open fact [2].`, hidden: ['Bluefin', 'e.g.'], kept: ['Open fact [2]'] },
  { name: 'decimal inside the sentence', text: `${SECRET} at 4.5 percent.[1] Open fact [2].`, hidden: ['Bluefin', '4.5'], kept: ['Open fact [2]'] },
  { name: 'initial inside the sentence', text: `${SECRET} per J. Smith. [1] Open fact [2].`, hidden: ['Bluefin', 'Smith'], kept: ['Open fact [2]'] },
  { name: 'sentence wrapped over a line break, cited once at the end', text: `${SECRET}\nand the Bluefin target.[1]\nOpen fact [2].`, hidden: ['Bluefin', 'target'], kept: ['Open fact [2]'] },
  { name: 'bullet', text: `- ${SECRET}.[1]\n- Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'numbered item', text: `1. ${SECRET}. [1]\n2. Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'quote', text: `> ${SECRET}.[1]\n\nOpen fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
  { name: 'question and exclamation', text: `Is the codename Bluefin?[1] Yes! [1] Open fact [2].`, hidden: ['Bluefin', 'Yes'], kept: ['Open fact [2]'] },
  { name: 'closing quote', text: `He said "${SECRET}." [1] Open fact [2].`, hidden: ['Bluefin'], kept: ['Open fact [2]'] },
]

const viewer = { userId: 'viewer', orgId: 'org' }
const config = { aiBackend: 'http://ai.test', jwtSecret: 's', scopedJwtSecret: 's' } as never

describe('citation placement: a sentence is hidden whether its citation sits before or after the stop', () => {
  afterEach(() => sinon.restore())

  for (const form of FORMS) {
    describe(form.name, () => {
      const check = (text: string) => {
        for (const h of form.hidden) expect(text, `hidden: ${h}`).to.not.include(h)
        for (const k of form.kept ?? []) expect(text, `kept: ${k}`).to.include(lk(k))
      }

      it('viewer response', () => {
        const [out] = redactMessagesForViewer([message(form.text)], new Set(['r2']))
        check(out.content)
        check(JSON.stringify(out))
      })

      it('model history', async () => {
        sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: ['r2'] } } as never)
        const out = await redactMessagesFor(config, [message(form.text)], 'author', viewer)
        const history = JSON.stringify(formatPreviousConversations(out as never))
        for (const h of form.hidden) expect(history, `hidden: ${h}`).to.not.include(h)
      })

      it('SSE complete frame', async () => {
        sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { accessible: ['r2'] } } as never)
        const frame = await redactConversationFor(config, { initiator: 'author', messages: [message(form.text)] }, viewer)
        const json = `event: complete\ndata: ${JSON.stringify(frame)}\n\n`
        for (const h of form.hidden) expect(json, `hidden: ${h}`).to.not.include(h)
      })
    })
  }
})

describe('citation placement: random layouts never leak a sentence whose sources are all forbidden', () => {
  // mulberry32, so a failure reproduces from the printed seed
  const rng = (seed: number) => () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  it('hides forbidden sentences in 400 generated answers', () => {
    for (let seed = 1; seed <= 400; seed += 1) {
      const rand = rng(seed)
      const pick = <T,>(xs: T[]): T => xs[Math.floor(rand() * xs.length)]!
      const forbidden = new Set([1, 3])
      let text = ''
      const secrets: string[] = []
      const count = 2 + Math.floor(rand() * 5)
      for (let i = 0; i < count; i += 1) {
        const secret = rand() < 0.5
        const n = secret ? pick([1, 3]) : 2
        const token = `SECRET${seed}x${i}`
        const filler = pick(['', ' e.g. a case', ' at 4.5 percent', ' per J. Smith', ' and more'])
        const body = `${secret ? token : `Public${i}`} fact${filler}`
        const markers = `[${n}]` + (secret && rand() < 0.3 ? `[${pick([1, 3])}]` : '')
        const link = rand() < 0.4 ? `(http://h/record/r${n}/preview#b=${i})` : ''
        const mark = markers.replace(/\](?!.*\])/, `]${link}`)
        const layout = Math.floor(rand() * 5)
        const unit = [
          `${body} ${mark}.`,
          `${body}. ${mark}`,
          `${body}.${mark}`,
          `${body}. ${mark}.`,
          `${body}.${mark}.`,
        ][layout]!
        if (secret) secrets.push(token)
        const sep = pick([' ', ' ', '\n', '\n\n'])
        text += (i ? sep : '') + (rand() < 0.15 ? `- ${unit}` : unit)
      }
      const out = redactAnswerText(text, forbidden)
      for (const token of secrets) {
        expect(out, `seed ${seed}\n${text}`).to.not.include(token)
      }
      expect(out, `seed ${seed}`).to.not.match(/http:\/\/h\/record\/r[13]/)
    }
  })
})
