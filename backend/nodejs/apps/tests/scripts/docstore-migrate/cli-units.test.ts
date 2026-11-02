import 'reflect-metadata'
import { expect } from 'chai'
import { UsageError, numFlag, parse, runCli, strFlag } from '../../../src/scripts/docstore-migrate/cli'

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

  it('help succeeds and an unknown command exits 2', async () => {
    expect(await run('help')).to.equal(0)
    expect(await run('verify', '--help')).to.equal(0)
    for (const cmd of ['nonsense']) expect(await run(cmd), cmd).to.equal(2)
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
      ['follow', 'run', '--until-idle', 'abc'],
      ['reverse', '--until-idle', 'abc'],
      ['rollback', '--online', '--max-freeze', '-1'],
      ['verify', '--sample', '2'],
      ['verify', '--sample', 'x'],
      ['bulk', '--stop-after-batches', '0'],
      ['cutover', '--max-freeze', 'Infinity'],
    ])
      expect(await run(...argv), argv.join(' ')).to.equal(2)
  })
})
