import 'reflect-metadata'
import { expect } from 'chai'
import { FLAGS, HELP, UsageError, VALUE_FLAGS, checkFlags, numFlag, parse, runCli, strFlag } from '../../../src/scripts/docstore-migrate/cli'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'

const flag = (...argv: string[]) => parse(['drain', ...argv])

describe('command line flags', () => {
  it('a text operand must be there: no value, an empty one, or a flag in its place is refused', () => {
    expect(strFlag(flag(), 'only')).to.equal(undefined)
    expect(strFlag(flag('--only', 'users,org'), 'only')).to.equal('users,org')
    for (const bad of [flag('--only'), flag('--only', ''), flag('--only', '   '), flag('--only', '--force')])
      expect(() => strFlag(bad, 'only')).to.throw(UsageError, '--only needs a value')
  })

  it('a number operand must be a finite number inside the range of the option', () => {
    const wait = (...argv: string[]) => numFlag(flag(...argv), 'wait', { min: 0, max: 100 })
    expect(wait()).to.equal(undefined)
    expect(wait('--wait', '0')).to.equal(0)
    expect(wait('--wait', '2.5')).to.equal(2.5)
    for (const bad of [['--wait'], ['--wait', '']]) expect(() => wait(...bad), bad.join(' ')).to.throw(UsageError, '--wait needs a value')
    for (const bad of [['--wait', 'abc'], ['--wait', 'Infinity'], ['--wait', 'NaN'], ['--wait', '-1'], ['--wait', '101']])
      expect(() => wait(...bad), bad.join(' ')).to.throw(UsageError, '--wait must be a number from 0 to 100')
  })

  it('a whole number must be whole', () => {
    expect(numFlag(flag('--n', '3'), 'n', { min: 1, int: true })).to.equal(3)
    expect(() => numFlag(flag('--n', '1.5'), 'n', { min: 1, int: true })).to.throw(UsageError, 'whole number')
  })
})

describe('what the command line refuses before it reads a configuration or opens a connection', () => {
  // no environment at all: any attempt to reach a server would throw instead of returning a code
  const run = (...argv: string[]) => {
    const w = process.stdout.write
    const e = process.stderr.write
    process.stdout.write = (() => true) as never
    process.stderr.write = (() => true) as never
    return runCli(argv, {}).finally(() => {
      process.stdout.write = w
      process.stderr.write = e
    })
  }

  it('help succeeds, an unknown command and the way back that is not here yet exit 2', async () => {
    expect(await run('help')).to.equal(0)
    expect(await run('verify', '--help')).to.equal(0)
    for (const cmd of ['nonsense', 'rollback', 'reverse']) expect(await run(cmd), cmd).to.equal(2)
  })

  it('reset without --yes and migrate without --online or --offline exit 2', async () => {
    expect(await run('reset')).to.equal(2)
    expect(await run('migrate')).to.equal(2)
  })

  it('a flag that cannot be read exits 2: a drain with a wait that is not a number does not interrupt anything', async () => {
    for (const argv of [
      ['drain', '--wait', 'abc', '--force'],
      ['drain', '--wait', '-5'],
      ['bulk', '--only'],
      ['verify', '--only', 'nosuch'],
      ['bulk', '--only', `${MAPPINGS[0].collection}, nosuch`],
      ['follow', 'run', '--until-idle', 'abc'],
      ['verify', '--sample', '2'],
      ['verify', '--sample', 'x'],
      ['bulk', '--stop-after-batches', '0'],
      ['cutover', '--max-freeze', 'Infinity'],
    ])
      expect(await run(...argv), argv.join(' ')).to.equal(2)
  })
})

describe('flags the command line does not have, and words that do not go together', () => {
  const ok = (...argv: string[]) => checkFlags(parse(argv))

  it('every flag a command has is accepted, with --help allowed everywhere', () => {
    for (const [cmd, flags] of Object.entries(FLAGS)) {
      const sub = cmd === 'follow' ? ['run'] : []
      ok(cmd, ...sub)
      for (const f of flags) if (!(cmd === 'migrate' && f === 'online')) ok(cmd, ...sub, `--${f}`)
      ok(cmd, ...sub, '--help')
    }
  })

  it('every flag the help text documents belongs to a command', () => {
    const documented = new Set([...HELP.matchAll(/--([a-z][a-z-]*)/g)].map((m) => m[1]!))
    const known = new Set(Object.values(FLAGS).flat())
    for (const f of documented) if (f !== 'help') expect(known.has(f), `--${f}`).to.equal(true)
  })

  it('a flag the command does not have is refused: a typo is not ignored', () => {
    expect(() => ok('migrate', '--offline', '--dryrun')).to.throw(UsageError, "--dryrun is not a flag of 'migrate'")
    expect(() => ok('bulk', '--onyl', 'x')).to.throw(UsageError, "--onyl is not a flag of 'bulk'")
    expect(() => ok('verify', '--force')).to.throw(UsageError, "--force is not a flag of 'verify'")
    expect(() => ok('status', '--yes')).to.throw(UsageError, 'it has none')
  })

  it('a switch takes no value: --force false does not turn force on', () => {
    expect(() => ok('reset', '--yes', '--force', 'false')).to.throw(UsageError, '--force takes no value')
    expect(() => ok('migrate', '--offline', '--allow-writers', 'false')).to.throw(UsageError, '--allow-writers takes no value')
    expect(() => ok('reset', '--yes', 'no')).to.throw(UsageError, '--yes takes no value')
    ok('reset', '--yes', '--force')
    // every flag of every command is either a switch or listed as taking a value, and each kind parses as such
    for (const [cmd, flags] of Object.entries(FLAGS))
      for (const k of flags) {
        if (VALUE_FLAGS.has(k)) expect(() => ok(cmd, `--${k}`, 'x'), `${cmd} --${k} x`).to.not.throw(/takes no value/)
        else expect(() => ok(cmd, `--${k}`, 'x'), `${cmd} --${k} x`).to.throw(UsageError, 'takes no value')
      }
  })

  it('follow wants start or run, and migrate one mode, and an incremental verify has no collection scope', () => {
    for (const bad of [['follow'], ['follow', 'stop']]) expect(() => ok(...bad), bad.join(' ')).to.throw(UsageError, 'follow needs')
    expect(() => ok('migrate', '--offline', '--online')).to.throw(UsageError, 'not both')
    expect(() => ok('verify', '--incremental', '--only', 'users')).to.throw(UsageError, 'no collection scope')
    ok('follow', 'start')
    ok('verify', '--incremental', '--sample', '0.1')
  })

  it('each of them exits 2 from the command line before anything is read', async () => {
    const w = process.stdout.write
    const e = process.stderr.write
    process.stdout.write = (() => true) as never
    process.stderr.write = (() => true) as never
    try {
      for (const argv of [
        ['migrate', '--offline', '--dryrun'],
        ['bulk', '--onyl', 'x'],
        ['verify', '--force'],
        ['follow'],
        ['migrate', '--offline', '--online'],
        ['verify', '--incremental', '--only', 'users'],
      ])
        expect(await runCli(argv, {}), argv.join(' ')).to.equal(2)
    } finally {
      process.stdout.write = w
      process.stderr.write = e
    }
  })
})
