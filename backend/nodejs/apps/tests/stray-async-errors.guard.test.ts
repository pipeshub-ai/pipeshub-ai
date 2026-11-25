import { expect } from 'chai'
import { spawnSync } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

// Two workers: the first file leaves a timer that rejects after it ends; the second file holds the other
// worker, so the third loads on the first worker while that timer fires (mocha has removed its listeners by then).
describe('stray async error guard', function () {
  this.timeout(120000)

  const run = (guarded: boolean): { status: number | null; out: string } => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'stray-run-'))
    const leaver = path.join(dir, 'a-leaves-work.test.ts')
    fs.writeFileSync(leaver, `
      it('starts a retry it never stops', () => {
        setTimeout(() => { Promise.reject(new Error('late retry failed')) }, 200)
      })
    `)
    const busy = path.join(dir, 'busy.test.ts')
    fs.writeFileSync(busy, `it('holds the other worker', () => new Promise((resolve) => setTimeout(resolve, 1000)))`)
    // ES module top-level await: the worker idles while it loads, as it does compiling a large file.
    const slow = path.join(dir, 'b-loads-slowly.test.mjs')
    fs.writeFileSync(slow, `
      await new Promise((resolve) => setTimeout(resolve, 1500))
      it('is innocent', () => undefined)
    `)
    const root = path.resolve(__dirname, '..')
    const result = spawnSync(
      process.execPath,
      [
        path.join(root, 'node_modules', 'mocha', 'bin', 'mocha.js'), '--no-config', '--parallel', '--jobs', '2',
        '--require', 'ts-node/register',
        ...(guarded ? ['--require', path.join(root, 'tests', 'helpers', 'stray-async-errors-global.ts')] : []),
        '--reporter', 'spec', leaver, busy, slow,
      ],
      {
        cwd: root,
        // Not inherited from this run's own worker: the child's main process is not a worker.
        env: { ...process.env, MOCHA_WORKER_ID: undefined, TS_NODE_PROJECT: 'tsconfig.test.json', TS_NODE_TRANSPILE_ONLY: 'true' },
        encoding: 'utf8',
      },
    )
    return { status: result.status, out: `${result.stdout}${result.stderr}` }
  }

  it('without it, the worker dies and no test is named', () => {
    const { status, out } = run(false)
    expect(status, out).to.not.equal(0)
    expect(out).to.include('Workerpool Worker terminated Unexpectedly')
  })

  it('with it, the next file fails naming the file that left the work, and the worker lives', () => {
    const { status, out } = run(true)
    expect(status, out).to.equal(1)
    expect(out).to.not.include('Workerpool Worker terminated Unexpectedly')
    expect(out).to.include('unhandledRejection after')
    expect(out).to.include('a-leaves-work.test.ts finished: Error: late retry failed')
    expect(out).to.include('"after all" hook')
    expect(out).to.include('is innocent')
  })
})
