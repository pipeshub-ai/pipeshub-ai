import { expect } from 'chai'
import { readFileSync, readdirSync, statSync } from 'fs'
import { join, relative, sep } from 'path'

/**
 * Only the row writer writes message rows.
 *
 * Every row write must take a change stamp from its session's `rev`, or a reader that asks
 * what changed after a `rev` misses it for good. The row writer (`appendMessages`,
 * `updateMessageById`, `setMessageFields`, `retireMessagesById`) does that; a direct model
 * write elsewhere would not. This walks the source, because such a write reads as harmless
 * where it is made.
 *
 * A write that must not be stamped says so on the line above it, with a reason:
 * `// row-writer-exempt: <why>`.
 *
 * shortcut: a document's own `save()` on a loaded row is not caught; add it if one ever appears.
 */

const SRC = join(__dirname, '..', '..', '..', '..', 'src')
const ROW_WRITER = join('modules', 'enterprise_search', 'utils', 'utils.ts')

const WRITE =
  /\bChatSessionMessage\s*\.\s*(?:collection\b|insertMany|insertOne|create|updateOne|updateMany|replaceOne|findOneAndUpdate|findByIdAndUpdate|findOneAndReplace|findOneAndDelete|findByIdAndDelete|deleteOne|deleteMany|bulkWrite)\b|\bnew\s+ChatSessionMessage\s*\(|collection\(\s*['"`]chatSessionMessages['"`]\s*\)/g
const EXEMPT = /\/\/\s*row-writer-exempt:\s*\S/

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return full.endsWith('.ts') ? [full] : []
  })

/** Every direct write to the message collection, as `path:line`, with whether it is the row writer's or marked exempt. */
const directWrites = (): Array<{ at: string; allowed: boolean }> =>
  walk(SRC).flatMap((file) => {
    const path = relative(SRC, file)
    const text = readFileSync(file, 'utf8')
    const lines = text.split('\n')
    return [...text.matchAll(WRITE)].flatMap((match) => {
      const line = text.slice(0, match.index).split('\n').length
      // A mention in a comment writes nothing.
      if (/^\s*(?:\*|\/\/|\/\*)/.test(lines[line - 1] ?? '')) return []
      const above = lines[line - 2] ?? ''
      return [{ at: `${path.split(sep).join('/')}:${String(line)}`, allowed: path === ROW_WRITER || EXEMPT.test(above) }]
    })
  })

describe('row writer inventory', () => {
  let writes: Array<{ at: string; allowed: boolean }> = []
  before(() => {
    writes = directWrites()
  })

  it('finds the row writer’s own writes, so the scan is looking in the right place', () => {
    const own = writes.filter((w) => w.at.startsWith(`${ROW_WRITER.split(sep).join('/')}:`))
    expect(own.length).to.be.greaterThan(0)
  })

  it('no message row is written outside the row writer unless marked exempt with a reason', () => {
    const offenders = writes
      .filter((w) => !w.allowed)
      .map((w) => w.at)
    expect(
      offenders,
      'Write message rows through appendMessages, updateMessageById, setMessageFields or retireMessagesById ' +
        '(utils.ts), which stamp each row; or mark a write that must stay unstamped with ' +
        '`// row-writer-exempt: <why>` on the line above:\n  ' +
        offenders.join('\n  '),
    ).to.deep.equal([])
  })
})
