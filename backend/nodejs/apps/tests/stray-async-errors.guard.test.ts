import { expect } from 'chai'
import { spawnSync } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

// Two workers: the first file leaves a timer that rejects after it ends; the second file holds the other
// worker, so a third file loads on the first worker while that timer fires (mocha has removed its listeners by then).
describe('stray async error guard', function () {
  this.timeout(120000)

  const FILES = {
    leaver: ['a-leaves-work.test.ts', `
      it('starts a retry it never stops', () => {
        setTimeout(() => { Promise.reject(new Error('late retry failed')) }, 200)
      })
    `],
    busy: ['busy.test.ts', `it('holds the other worker', () => new Promise((resolve) => setTimeout(resolve, 1000)))`],
    // ES module top-level await: the worker idles while it loads, as it does compiling a large file.
    slow: ['b-loads-slowly.test.mjs', `
      await new Promise((resolve) => setTimeout(resolve, 1500))
      it('is innocent', () => undefined)
    `],
    clearsAll: ['clears-all.test.ts', `
      it('removes every process listener when called with no event', () => {
        const kept = process.eventNames().map((name) => [name, process.rawListeners(name)] as const)
        process.on('beforeExit', () => undefined)
        process.removeAllListeners()
        const left = process.eventNames().filter((name) => name !== 'uncaughtException' && name !== 'unhandledRejection')
        // Put back the worker's own listeners (its IPC channel), so the run can finish.
        for (const [name, fns] of kept) for (const fn of fns) process.on(name, fn as (...args: unknown[]) => void)
        if (left.length > 0) throw new Error('listeners kept: ' + left.map(String).join(', '))
      })
    `],
  } as const

  const run = (
    guarded: boolean,
    files: (keyof typeof FILES)[],
    extra: string[] = [],
  ): { status: number | null; out: string } => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'stray-run-'))
    const paths = files.map((key) => {
      const [name, body] = FILES[key]
      fs.writeFileSync(path.join(dir, name), body)
      return path.join(dir, name)
    })
    const root = path.resolve(__dirname, '..')
    const result = spawnSync(
      process.execPath,
      [
        path.join(root, 'node_modules', 'mocha', 'bin', 'mocha.js'), '--no-config', '--parallel', '--jobs', '2',
        '--require', 'ts-node/register',
        ...(guarded ? ['--require', path.join(root, 'tests', 'helpers', 'stray-async-errors-global.ts')] : []),
        '--reporter', 'spec', ...extra, ...paths,
      ],
      {
        cwd: root,
        // Not inherited from this run: the child's main process is not a worker and keeps its own report folder.
        env: {
          ...process.env,
          MOCHA_WORKER_ID: undefined,
          STRAY_ASYNC_ERRORS_DIR: undefined,
          TS_NODE_PROJECT: 'tsconfig.test.json',
          TS_NODE_TRANSPILE_ONLY: 'true',
        },
        encoding: 'utf8',
      },
    )
    return { status: result.status, out: `${result.stdout}${result.stderr}` }
  }

  it('without it, the worker dies and no test is named', () => {
    const { status, out } = run(false, ['leaver', 'busy', 'slow'])
    expect(status, out).to.not.equal(0)
    expect(out).to.include('Workerpool Worker terminated Unexpectedly')
  })

  it('with it, the next file fails naming the file that left the work, and the worker lives', () => {
    const { status, out } = run(true, ['leaver', 'busy', 'slow'])
    expect(status, out).to.equal(1)
    expect(out).to.not.include('Workerpool Worker terminated Unexpectedly')
    expect(out).to.include('a-leaves-work.test.ts finished: Error: late retry failed')
    expect(out).to.include('"after all" hook')
    expect(out).to.include('is innocent')
    // Reported once: the after-all hook took it, so the end of the run has nothing left to report.
    expect(out).to.not.include('global teardown failed')
  })

  it('the file that left the work is its worker\'s last: the run fails at its end, naming the file', () => {
    const { status, out } = run(true, ['leaver', 'busy'])
    expect(status, out).to.not.equal(0)
    expect(out).to.include('global teardown failed')
    expect(out).to.include('a-leaves-work.test.ts finished: Error: late retry failed')
  })

  it('every later file in that worker selects no test (--grep): the run fails at its end, naming the file', () => {
    const { status, out } = run(true, ['leaver', 'busy', 'slow'], ['--grep', 'retry|other worker'])
    expect(status, out).to.not.equal(0)
    expect(out).to.not.include('is innocent')
    expect(out).to.include('global teardown failed')
    expect(out).to.include('a-leaves-work.test.ts finished: Error: late retry failed')
  })

  it('process.removeAllListeners() with no event still removes every listener', () => {
    const { status, out } = run(true, ['clearsAll'])
    expect(status, out).to.equal(0)
    expect(out).to.include('1 passing')
  })
})
