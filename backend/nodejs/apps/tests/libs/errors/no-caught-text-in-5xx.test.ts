import { expect } from 'chai'
import { readFileSync, readdirSync, statSync } from 'fs'
import { join } from 'path'

/**
 * A 500 must not repeat what the failure said.
 *
 * `InternalServerError` is an `HttpError`, and the error middleware treats any
 * `HttpError` as wording this codebase chose, so whatever is passed here goes
 * straight to the person who made the request. Building the message out of a
 * caught exception therefore puts a stack-shaped string, a driver's complaint
 * or another service's internals on their screen.
 *
 * This walks the source rather than any one call path, because the mistake is
 * easy to repeat and reads as helpful while you are writing it.
 */

const SRC = join(__dirname, '..', '..', '..', 'src')

/** Reads the whole argument list, including the nested parentheses inside it. */
const argumentsOf = (text: string, openParen: number): string => {
  let depth = 0
  for (let i = openParen; i < text.length; i += 1) {
    if (text[i] === '(') depth += 1
    else if (text[i] === ')') {
      depth -= 1
      if (depth === 0) return text.slice(openParen, i + 1)
    }
  }
  return text.slice(openParen)
}

/**
 * The shapes that put a failure's own words in the argument: a caught error's
 * `.message`, a stringified error, the message another service sent back, or a
 * `data` field a helper packed the failure into on its way out - which is how
 * the mail library's complaint reached people before this test existed.
 */
const REPEATS_THE_FAILURE =
  /\b(?:error|err|storageError|usageError|cause)\b\s*\??\.?\s*(?:instanceof\s+Error\s*\?\s*\w+\.message|\.message)|response\??\.\s*data\??\.\s*error|\bresult\s*\??\.\s*data\b|String\(\s*(?:error|err)\s*\)/

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return full.endsWith('.ts') ? [full] : []
  })

describe('a 500 never repeats what the failure said', () => {
  it('builds no InternalServerError out of caught or upstream text', () => {
    const offenders: string[] = []

    for (const file of walk(SRC)) {
      // The mapper in libs/errors is where upstream text is judged; it reads
      // those fields on purpose and decides what may be passed on.
      if (file.includes(join('libs', 'errors'))) continue

      const text = readFileSync(file, 'utf8')
      const marker = 'new InternalServerError('
      for (let at = text.indexOf(marker); at !== -1; at = text.indexOf(marker, at + 1)) {
        const args = argumentsOf(text, at + marker.length - 1)
        if (REPEATS_THE_FAILURE.test(args)) {
          const line = text.slice(0, at).split('\n').length
          offenders.push(`${file.slice(SRC.length + 1)}:${line}`)
        }
      }
    }

    expect(
      offenders,
      'Log the failure and give the person a written sentence instead - ' +
        'serverFailureMessage() in libs/errors/reader-friendly is the one to use:\n  ' +
        offenders.join('\n  '),
    ).to.deep.equal([])
  })
})
