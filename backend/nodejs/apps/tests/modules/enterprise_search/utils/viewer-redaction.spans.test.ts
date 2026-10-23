import { expect } from 'chai'
import {
  REDACTED_TOKEN,
  redactAnswerText,
  redactMessagesForViewer,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'

/**
 * D-22 refinement 2: fail closed. In a turn that used any source the viewer cannot
 * read, only spans whose group has a readable citation (canonical link form,
 * outside code) survive; everything else becomes the redacted notice. A span is the
 * text from the end of the previous citation group (or the block start) up to and
 * including the next group; blocks are never crossed.
 */
const U = new Set([1])
const L = (n: number) => `[${n}](http://h:13600/record/rec${n}/preview#blockIndex=0)`
// The message has citations 1 and 2. Only `[n](own record url)` is canonical; any other
// form of a real number ends a span as unreadable; other numbers are plain text.
const real = (n: number, target?: string) =>
  n === 1 || n === 2 ? (target !== undefined && target.includes(`/record/rec${n}/`) ? true : 'unreadable') : false
const leaks = (out: string) => /SECRETX/.test(out) || /rec1\//.test(out)
const T = (rows: string[]) => '| Fact |\n|---|\n' + rows.map((r) => `| ${r} |`).join('\n')

// Every adversarial layout QA found over four rounds. Each must show no private text.
const LAYOUTS: Array<[string, string]> = [
  // round 1-2 (r2/adv.ts)
  ['lowercase next sentence', `Secret SECRETX fact ${L(1)}. then the open fact ${L(2)}.`],
  ['abbreviation Inc.', `The target is Wombat Inc. SECRETX ${L(1)} Open fact ${L(2)}.`],
  ['single-letter initial', `Codename SECRETX is held by J. ${L(1)} Open fact ${L(2)}.`],
  ['two lines, no stops', `Budget: SECRETX forty two million ${L(1)}\nHours: nine to five ${L(2)}`],
  ['bold label lines', `**Budget:** SECRETX ${L(1)}\n**Hours:** open ${L(2)}`],
  ['emoji instead of stop', `Secret SECRETX fact ${L(1)} 🎉 Open fact ${L(2)} 🎉`],
  ['ellipsis U+2026', `Secret SECRETX fact ${L(1)}… Open fact ${L(2)}…`],
  ['semicolon join', `Secret SECRETX fact ${L(1)}; open fact ${L(2)}.`],
  ['colon then newline', `Secret SECRETX fact ${L(1)}:\nOpen fact ${L(2)}.`],
  ['leading citation on next sentence', `Open fact ${L(2)}. ${L(1)} Secret SECRETX fact.`],
  ['marker mid-sentence', `Secret SECRETX ${L(1)} and open ${L(2)} fact.`],
  ['after heading', `## Findings\nSecret SECRETX fact ${L(1)}. Open fact ${L(2)}.`],
  ['heading itself cited', `## SECRETX budget ${L(1)}\nOpen fact ${L(2)}.`],
  ['table row mixed', `| Budget | SECRETX ${L(1)} |\n| Hours | open ${L(2)} |`],
  ['table one row both cells', `| SECRETX ${L(1)} | open ${L(2)} |`],
  ['quotes and parens', `("Secret SECRETX fact" ${L(1)}.) Open fact ${L(2)}.`],
  ['question/exclaim', `Is it SECRETX ${L(1)}? Yes! Open fact ${L(2)}!`],
  ['CJK full stop', `秘密 SECRETX 事实${L(1)}。公开事实${L(2)}。`],
  ['CJK no space comma', `秘密 SECRETX 事实${L(1)}，公开事实${L(2)}。`],
  ['code block', '```\nSECRETX = 42 ' + L(1) + '\nopen = 1 ' + L(2) + '\n```'],
  ['very long sentence', `Secret ${'SECRETX '.repeat(200)}fact ${L(1)}. Open fact ${L(2)}.`],
  ['markdown link non-citation', `See [the SECRETX plan](http://x/a.b.pdf) ${L(1)}. Open fact ${L(2)}.`],
  ['decimal', `Budget SECRETX is 4.2M ${L(1)}. Open fact ${L(2)}.`],
  ['bullet item', `- Secret SECRETX ${L(1)}\n- Open ${L(2)}`],
  ['numbered then plain line', `1. Secret SECRETX ${L(1)}\ncontinues here\n2. Open ${L(2)}`],
  ['url in text not link', `Secret SECRETX at http://x.y/z ${L(1)}. Open fact ${L(2)}.`],
  ['double bracket', `Secret SECRETX fact. [[1]] Open fact [[2]].`],
  ['only cite glued', `Secret SECRETX fact.${L(1)}Open fact ${L(2)}.`],
  ['markers on their own line', `Secret SECRETX fact\n${L(1)}\nOpen fact ${L(2)}.`],
  // round 3 (r2/adv3.ts)
  ['paragraph-end citation', `Line one SECRETX\nline two SECRETX ${L(1)}\n\nOpen para ${L(2)}`],
  ['list-end citation', `- a SECRETX\n- b SECRETX ${L(1)}\n\nOpen ${L(2)}`],
  ['private line between readable lines', `Open A ${L(2)}\nSecret SECRETX ${L(1)}\nOpen B ${L(2)}`],
  ['uncited private before private cite', `Open A ${L(2)}\nSECRETX uncited line\nSecret ${L(1)}`],
  ['nested list', `- top ${L(2)}\n  - nested SECRETX ${L(1)}\n- open ${L(2)}`],
  ['blockquote', `> SECRETX quote ${L(1)}\n> open ${L(2)}`],
  ['html inline', `<b>SECRETX</b> ${L(1)}<br>Open ${L(2)}`],
  ['html block', `<p>SECRETX ${L(1)}</p>\n<p>open ${L(2)}</p>`],
  ['CRLF lines', `Open A ${L(2)}\r\nSecret SECRETX ${L(1)}\r\nOpen B ${L(2)}\r\n`],
  ['CRLF paragraph end', `one SECRETX\r\ntwo SECRETX ${L(1)}\r\n`],
  ['marker then text no space', `SECRETX fact ${L(1)}Open ${L(2)}`],
  ['heading without marker then cited body', `## SECRETX heading\nBody text ${L(1)}`],
  ['uncited private paragraph, blank line before the cite', `SECRETX uncited\n\nOpen ${L(2)}`],
  // round 3-4 (table / code / marker forms)
  ['table cited below', `${T(['SECRETX row'])}\n\n${L(1)}`],
  ['table with a caption below', `${T(['SECRETX row'])}\nSource: board doc ${L(1)}`],
  ['table with a long caption below', `${T(['SECRETX row'])}\nSource: ${'the board document that describes the budget '.repeat(3)}${L(1)}`],
  ['two tables, one citation line (QA-W20-13)', `${T(['SECRETX row A'])}\n\n${T(['SECRETX row B'])}\n\n${L(1)}`],
  ['table, heading, citation (QA-W20-13)', `${T(['SECRETX row'])}\n\n## Sources\n${L(1)}`],
  ['table per-cell plus trailing', `| SECRETX a | open ${L(2)} |\n|---|---|\n| SECRETX b | SECRETX c |\n\n${L(1)}`],
  ['table, trailing mixed group', `${T(['SECRETX row'])}\n\n${L(1)} ${L(2)}`],
  ['private uncited table, readable trailing citation', `${T(['SECRETX row'])}\n\n${L(2)}`],
  ['code block cited below', '```\nSECRETX = 42\n```\n' + L(1)],
  ['code block, blank, cited below', '```\nSECRETX = 42\n```\n\n' + L(1)],
  ['code with arr[2], cited below (QA-W20-14)', '```\nSECRETX = arr[2]\n```\n' + L(1)],
  ['code with a real-looking link, cited below (QA-W20-14)', '```\nSECRETX ' + L(2) + '\n```\n' + L(1)],
  ['code with a readable citation only', '```\nSECRETX ' + L(2) + '\n```'],
  ['table cell with bare [2] and trailing', `${T(['SECRETX arr[2]'])}\n\n${L(1)}`],
  ['bare [2] before the real cite (QA-W20-14)', `SECRETX in arr[2] slot ${L(1)}. Open ${L(2)}`],
  ['[[2]] before the real cite', `SECRETX fact [[2]] then ${L(1)}`],
  ['bracket number is not a citation', `SECRETX in arr[7] slot ${L(1)}. Open ${L(2)}`],
  ['fact [9] with no such citation', `SECRETX fact [9] more ${L(1)}. Open ${L(2)}`],
  ['inline code marker', 'SECRETX value `x[2]` here ' + L(1) + '. Open ' + L(2)],
  ['inline code with a link', 'SECRETX `' + L(2) + '` here ' + L(1) + '. Open ' + L(2)],
  ['link to a foreign target', `SECRETX see [2](http://evil/x) and ${L(1)}. Open ${L(2)}`],
  ['double-bracket foreign link', `SECRETX [[2]](http://evil) then ${L(1)}`],
  ['link to the other record\'s url', `SECRETX see [2](http://h:13600/record/rec1/preview) ok. Open ${L(2)}`],
  ['[1] with a wrong url, alone', `SECRETX see [1](http://evil/y). Open ${L(2)}`],
  ['unresolved number with an own-looking url', `SECRETX [9](http://h:13600/record/rec2/x) ${L(1)}. Open ${L(2)}`],
  ['uncited text after the last group', `Open fact ${L(2)}. SECRETX afterwards`],
  ['only uncited text', `SECRETX nothing cited here`],
  ['bare marker only', `SECRETX fact [1]`],
]

describe('redactAnswerText: fail closed in a turn with an unreadable source', () => {
  for (const [name, text] of LAYOUTS) {
    it(name, () => {
      const out = redactAnswerText(text, U, real)
      expect(leaks(out), out).to.equal(false)
    })
  }

  it('keeps the readable span next to a redacted one, merging notices', () => {
    expect(redactAnswerText(`Secret fact. ${L(1)} Open fact ${L(2)}.`, U, real)).to.equal(
      `${REDACTED_TOKEN} Open fact ${L(2)}.`,
    )
    expect(redactAnswerText(`One ${L(1)}. Two ${L(1)}. Three ${L(2)}.`, U, real)).to.equal(
      `${REDACTED_TOKEN} Three ${L(2)}.`,
    )
  })

  it('shows only the readable citations of a mixed group', () => {
    expect(redactAnswerText(`Both agree ${L(1)}${L(2)}.`, U, real)).to.equal(`Both agree ${L(2)}.`)
  })

  it('keeps a readable table cell and a heading, redacts the rest of the table, and always drops code', () => {
    const table = redactAnswerText(`## Title ${L(2)}\n| a | SECRETX ${L(1)} | open ${L(2)} |\n|---|---|---|\n| uncited | x ${L(2)} | y |`, U, real)
    expect(table).to.include(`## Title ${L(2)}`)
    expect(table).to.include(`open ${L(2)}`)
    expect(table).to.include('|---|---|---|')
    expect(table).to.not.include('SECRETX')
    expect(table).to.not.include('uncited')
    expect(redactAnswerText('```\nx\n```\n' + `Fact ${L(2)}`, U, real)).to.equal(`${REDACTED_TOKEN}\nFact ${L(2)}`)
  })

  it('a hidden table reveals nothing about its shape: a 3x4 table reads like a one-line paragraph', () => {
    const grid = ['| a | b | c | d |', '|---|---|---|---|', '| SECRETX 1 | 2 | 3 | 4 |', '| 5 | 6 | 7 | 8 |', '| 9 | 10 | 11 | 12 |'].join('\n')
    const paragraph = redactAnswerText('SECRETX one hidden line', U, real)
    expect(redactAnswerText(grid, U, real)).to.equal(paragraph)
    expect(paragraph).to.equal(REDACTED_TOKEN)
    expect(redactAnswerText(`${grid}\n\n${L(1)}`, U, real)).to.equal(REDACTED_TOKEN)
  })

  it('consecutive hidden blocks become one notice when nothing visible lies between them', () => {
    const hidden = [`SECRETX para ${L(1)}`, '| a |\n|---|\n| SECRETX |', '```\nSECRETX\n```', `## SECRETX head ${L(1)}`, '- SECRETX item'].join('\n\n')
    expect(redactAnswerText(hidden, U, real)).to.equal(REDACTED_TOKEN)
    const around = redactAnswerText(`Open ${L(2)}\n\n${hidden}\n\nMore ${L(2)}`, U, real)
    expect(around).to.equal(`Open ${L(2)}\n\n${REDACTED_TOKEN}\n\nMore ${L(2)}`)
    // Something visible between two hidden blocks keeps them apart.
    expect(redactAnswerText(`SECRETX a ${L(1)}\n\nOpen ${L(2)}\n\nSECRETX b ${L(1)}`, U, real)).to.equal(
      `${REDACTED_TOKEN}\n\nOpen ${L(2)}\n\n${REDACTED_TOKEN}`,
    )
  })

  it('[[N]](own url) is not the canonical form: it ends the span as unreadable', () => {
    expect(leaks(redactAnswerText(`SECRETX fact [[2]](http://h:13600/record/rec2/preview#b=0). Open ${L(2)}`, U, real))).to.equal(false)
    const cites = (n: number, id: string) => ({ citationId: { _id: `c${n}`, chunkIndex: n, metadata: { recordId: id, webUrl: `https://app/${id}` } } })
    const msg = { _id: 'm', messageType: 'bot_response', content: 'SECRETX [[2]](https://app/rec2) end.', citations: [cites(1, 'rec1'), cites(2, 'rec2')] }
    expect(redactMessagesForViewer([msg], new Set(['rec2']))[0]!.content).to.not.include('SECRETX')
  })

  it('returns a turn the viewer can read in full unchanged, whatever its layout', () => {
    for (const [, text] of LAYOUTS) expect(redactAnswerText(text, new Set(), real)).to.equal(text)
  })

  it('judges markers by the message’s citations: only the canonical link form counts', () => {
    const cites = (n: number, id: string) => ({
      citationId: { _id: `c${n}`, chunkIndex: n, metadata: { recordId: id, webUrl: `https://app/${id}` } },
    })
    const msg = (content: string) => ({ _id: 'm', messageType: 'bot_response', content, citations: [cites(1, 'rec1'), cites(2, 'rec2')] })
    const run = (content: string) => redactMessagesForViewer([msg(content)], new Set(['rec2']))[0]!.content as string
    const own2 = '[2](http://h/record/rec2/preview#b=0)'
    expect(run(`SECRETX arr[7] [1](http://h/record/rec1/preview). Open ${own2}.`)).to.not.include('SECRETX')
    expect(run(`SECRETX [3] more [1](http://h/record/rec1/preview). Open ${own2}.`)).to.not.include('SECRETX')
    expect(run(`SECRETX [2] bare. Open ${own2}.`), 'bare [2] ends the span unreadable').to.not.include('SECRETX')
    expect(run('SECRETX `x[2]` more. Open ' + own2 + '.')).to.not.include('SECRETX')
    expect(run(`SECRETX [2](http://evil) later ${own2}.`)).to.not.include('SECRETX')
    expect(run(`Readable text [2](http://wrong/for-2) here.`), 'wrong url = over-redaction').to.not.include('Readable text')
    expect(run(`Open ${own2}. SECRETX uncited`)).to.equal(`Open ${own2}. ${REDACTED_TOKEN}`)
    // A number that is not one of the message's citations is plain text.
    expect(run(`Index arr[9] stays ${own2}.`)).to.equal(`Index arr[9] stays ${own2}.`)
  })

  it('judges a streamed answer on its completed text, whatever the chunking was', () => {
    const text = `Secret SECRETX ${L(1)}. Open fact ${L(2)}.`
    for (let cut = 1; cut < text.length; cut += 1) {
      // Chunks are only ever sent to the user the turn belongs to; foreign viewers get the whole message.
      expect(redactAnswerText(text.slice(0, cut) + text.slice(cut), U, real)).to.equal(redactAnswerText(text, U, real))
    }
  })
})

describe('redactAnswerText: random layouts keep only spans with a readable citation', () => {
  const rng = (seed: number) => () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const FORBIDDEN = [1, 3]
  const resolver = (n: number, target?: string) =>
    [1, 2, 3].includes(n) ? (target !== undefined && target.includes(`/record/r${n}/`) ? true : 'unreadable') : false

  it('output holds only text of units with a readable citation, plus notices (1500 answers)', () => {
    for (let seed = 1; seed <= 1500; seed += 1) {
      const rand = rng(seed)
      const pick = <T,>(xs: T[]): T => xs[Math.floor(rand() * xs.length)]!
      /** Tokens of units whose group has a readable citation: the only text allowed to survive. */
      const allowed = new Set<string>()
      let counter = 0
      let lastKeep = false
      const flags: boolean[] = []
      const cite = (n: number) =>
        pick([`[${n}](http://h/record/r${n}/preview#b=${seed})`, `[${n}](http://h/record/r${n}/preview#b=${seed})`, `[${n}]`, `[[${n}]]`])

      // One unit = body text + its citation group + what follows it.
      const unit = (): string => {
        const kind = pick(['forbidden', 'forbidden', 'readable', 'mixed'])
        const token = `SECRET${seed}x${counter++}Z`
        const body = pick([
          'Plain sentence',
          'lowercase start',
          'Acme Inc.',
          'per J. Smith',
          'approx. 4.5 percent',
          'e.g. a case',
          '🎉 launch',
          'Fakten…',
          '秘密事实',
          '**Label:** value',
          'A "quoted" claim',
        ])
        // Fake brackets, foreign links and bare markers in the text must not rescue it.
        const fake = pick(['', '', ' arr[7]', ' [9]', ' `x[2]`', ' [2](http://evil/x)', ' [[2]](http://evil)', ' [1](http://evil/y)', ' arr[2]'])
        let group = ''
        if (kind === 'forbidden') {
          group = cite(pick(FORBIDDEN)) + pick(['', '', cite(pick(FORBIDDEN)), ` ${cite(pick(FORBIDDEN))}`, `, ${cite(pick(FORBIDDEN))}`])
        } else if (kind === 'readable') {
          group = cite(2)
        } else {
          group = cite(pick(FORBIDDEN)) + cite(2)
        }
        const trailing = pick(['', '.', '!', '。', '…', ',', ';', '.', ' 🎉'])
        const glue = pick([' ', ' ', '', '　'])
        lastKeep = kind !== 'forbidden'
        flags.push(lastKeep)
        if (lastKeep) allowed.add(token)
        return `${body} ${token}${fake}${glue}${group}${trailing}`
      }
      const uncited = (): string => `${pick(['Plain', 'lowercase', 'Acme Inc.', '🎉'])} SECRET${seed}u${counter++}Z`

      const blocks: string[] = []
      const count = 1 + Math.floor(rand() * 6)
      for (let b = 0; b < count; b += 1) {
        const kind = pick(['line', 'line', 'bullet', 'numbered', 'heading', 'table', 'fence', 'wrapped', 'paragraph', 'listEnd', 'tableBelow', 'fenceBelow', 'uncited', 'twoTables'])
        flags.length = 0
        const units = Array.from({ length: 1 + Math.floor(rand() * 3) }, unit)
        const joined = units.join(pick([' ', ' ', '', '  ']))
        const cellsOf = (u: string[]) => `|${u.map((x) => ` ${x} `).join('|')}|`
        if (kind === 'bullet') blocks.push(`- ${joined}`)
        else if (kind === 'numbered') blocks.push(`${b + 1}. ${joined}`)
        else if (kind === 'heading') blocks.push(`## ${joined}`)
        else if (kind === 'table') blocks.push(`${cellsOf(units)}\n|${units.map(() => '---').join('|')}|\n${cellsOf(Array.from({ length: units.length }, uncited))}`)
        else if (kind === 'fence') blocks.push('```\n' + units.join('\n') + '\n```')
        else if (kind === 'wrapped') {
          const first = uncited()
          // Contiguous lines: uncited text before a kept span is part of that span.
          if (flags[0]) allowed.add(first.split(' ').pop()!)
          blocks.push(`${first}\n${joined}`)
        }
        else if (kind === 'paragraph' || kind === 'listEnd') {
          const dash = kind === 'listEnd' ? '- ' : ''
          const lines = Array.from({ length: 1 + Math.floor(rand() * 4) }, () => dash + uncited())
          const last = dash + unit()
          if (lastKeep) for (const l of lines) allowed.add(l.split(' ').pop()!)
          blocks.push([...lines, last].join('\n'))
        } else if (kind === 'tableBelow' || kind === 'fenceBelow' || kind === 'twoTables') {
          const rows = Array.from({ length: 1 + Math.floor(rand() * 3) }, uncited)
          const table = ['| Fact |', '|---|', ...rows.map((r) => `| ${r} |`)].join('\n')
          const second = kind === 'twoTables' ? `\n\n${['| Fact |', '|---|', `| ${uncited()} |`].join('\n')}` : ''
          const below = pick(['', '## Sources\n', 'Table 1 ', 'Source: ']) + cite(pick([...FORBIDDEN, 2]))
          blocks.push(
            (kind === 'fenceBelow' ? '```\n' + rows.join('\n') + '\n```' : table) + second + pick(['\n', '\n\n', '\n\n\n']) + below,
          )
        } else if (kind === 'uncited') blocks.push(uncited())
        else blocks.push(joined)
      }
      const text = blocks.join('\n\n')
      const out = redactAnswerText(text, new Set(FORBIDDEN), resolver)
      for (const token of out.match(/SECRET\d+[a-z]\d+Z/g) ?? []) {
        expect(allowed.has(token), `seed ${seed}: ${token} survived\n${text}\n--->\n${out}`).to.equal(true)
      }
      expect(out, `seed ${seed}`).to.not.match(/http:\/\/h\/record\/r[13]\//)
    }
  })
})
