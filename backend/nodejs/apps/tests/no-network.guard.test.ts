import { expect } from 'chai'
import { spawnSync } from 'child_process'
import * as fs from 'fs'
import * as net from 'net'
import * as os from 'os'
import * as path from 'path'
import { isLocal, NetworkGuard } from './helpers/no-network'

// A second guard under test, installed over a stand-in for the socket layer, so an admitted connect is
// recorded instead of leaving the host; addresses are from TEST-NET-1 (192.0.2.0/24).
describe('network guard (H9)', () => {
  let original: typeof net.Socket.prototype.connect
  let reached: unknown[][]
  let guard: NetworkGuard | null

  beforeEach(() => {
    original = net.Socket.prototype.connect
    reached = []
    net.Socket.prototype.connect = function standIn(this: net.Socket, ...args: unknown[]) {
      reached.push(args)
      return this
    } as typeof net.Socket.prototype.connect
    guard = null
  })

  afterEach(() => {
    guard?.uninstall()
    net.Socket.prototype.connect = original
  })

  const install = (mode: 'fail' | 'report', report: string | null = null): NetworkGuard => {
    guard = new NetworkGuard(mode, report)
    guard.install()
    return guard
  }

  const refusal = (socket: net.Socket): Promise<NodeJS.ErrnoException> =>
    new Promise((resolve) => socket.once('error', resolve))

  it('NG-N1 refuses a dial with ECONNREFUSED and records the test, address and stack', async () => {
    const g = install('fail')
    g.enter('suite dials out')
    const socket = new net.Socket()
    const pending = refusal(socket)
    socket.connect(443, '192.0.2.1')
    expect((await pending).code).to.equal('ECONNREFUSED')
    expect(reached).to.deep.equal([])
    const dials = g.leave()
    expect(dials.map((d) => [d.test, d.address])).to.deep.equal([['suite dials out', '192.0.2.1:443']])
    expect(dials[0]!.stack).to.include('no-network.guard.test')
  })

  it('NG-N2 lets loopback, localhost and Unix sockets through', () => {
    const g = install('fail')
    g.enter('local')
    new net.Socket().connect(80, '127.0.0.1')
    new net.Socket().connect({ host: 'localhost', port: 80 })
    new net.Socket().connect(80, '::1')
    new net.Socket().connect(80)
    new net.Socket().connect('/var/run/docker.sock')
    expect(reached).to.have.length(5)
    expect(g.leave()).to.deep.equal([])
  })

  it('NG-N3 lets a test tagged @network dial', () => {
    const g = install('fail')
    g.enter('live lane @network calls the provider')
    new net.Socket().connect(443, '192.0.2.1')
    expect(reached).to.have.length(1)
    expect(g.leave()).to.deep.equal([])
  })

  it('NG-N4 report mode lets the dial through and appends it to the report', () => {
    const report = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'ng-')), 'dials.jsonl')
    const g = install('report', report)
    g.enter('reported')
    new net.Socket().connect(443, '192.0.2.5')
    expect(reached).to.have.length(1)
    expect(g.leave()).to.have.length(1)
    const lines = fs.readFileSync(report, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
    expect(lines.map((l) => [l.mode, l.test, l.address])).to.deep.equal([['report', 'reported', '192.0.2.5:443']])
  })

  it('NG-N6 a dial outside any test is kept for the next test to report', () => {
    const g = install('fail')
    new net.Socket().on('error', () => undefined).connect(443, '192.0.2.9')
    g.enter('next test')
    const dials = g.leave()
    expect(dials.map((d) => d.test)).to.deep.equal([null])
  })

  it('NG-N9 off installs nothing', () => {
    expect(NetworkGuard.fromEnv({ TEST_NETWORK_GUARD: 'off' })).to.equal(null)
    expect(NetworkGuard.fromEnv({})?.mode).to.equal('fail')
    expect(() => NetworkGuard.fromEnv({ TEST_NETWORK_GUARD: 'loud' })).to.throw('fail, report or off')
  })

  it('NG-N10 what counts as local', () => {
    const table: [string | undefined, boolean][] = [
      [undefined, true], ['127.0.0.1', true], ['127.8.9.10', true], ['::1', true], ['[::1]', true],
      ['::ffff:127.0.0.1', true], ['localhost', true], ['api.localhost.', true],
      ['10.215.0.2', false], ['0.0.0.0', false], ['example.com', false], ['::ffff:192.0.2.1', false],
    ]
    expect(table.map(([host]) => isLocal(host))).to.deep.equal(table.map(([, local]) => local))
  })
})

describe('network guard root hooks (H9)', function () {
  this.timeout(120000)

  const run = (spec: string): { status: number | null; out: string } => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-run-'))
    const file = path.join(dir, 'probe.test.ts')
    fs.writeFileSync(file, spec)
    const root = path.resolve(__dirname, '..')
    const result = spawnSync(
      process.execPath,
      [
        path.join(root, 'node_modules', 'mocha', 'bin', 'mocha.js'), '--no-config', '--no-parallel',
        '--require', 'ts-node/register', '--require', path.join(root, 'tests', 'helpers', 'no-network-global.ts'),
        '--reporter', 'spec', file,
      ],
      {
        cwd: root,
        env: { ...process.env, TS_NODE_PROJECT: 'tsconfig.test.json', TS_NODE_TRANSPILE_ONLY: 'true', TEST_NETWORK_GUARD: 'fail', TEST_NETWORK_GUARD_REPORT: '' },
        encoding: 'utf8',
      },
    )
    return { status: result.status, out: `${result.stdout}${result.stderr}` }
  }

  it('NG-N5 fails a test that dialled even when it swallowed the refusal, and only that test', () => {
    const { status, out } = run(`
      import * as net from 'net'
      const dial = (done: () => void) => {
        const s = new net.Socket()
        s.on('error', () => done())
        s.on('connect', () => { s.destroy(); done() })
        s.connect(9, '192.0.2.1')
      }
      describe('quiet', () => {
        it('stays on the host', () => undefined)
      })
      describe('probe', () => {
        it('dials and swallows', (done) => dial(done))
      })
    `)
    expect(status, out).to.equal(1)
    expect(out).to.include('1 network dial(s) refused')
    expect(out).to.include('192.0.2.1:9')
    expect(out).to.include('1 failing')
    expect(out).to.include('"after each" hook: afterEach for "dials and swallows"')
  })
})
