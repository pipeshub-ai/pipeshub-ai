/**
 * No test reaches past the host unless its title says `@network` (H9; the Python suite's
 * tests/support/no_network.py does the same).
 *
 * Every `net.Socket#connect` (http, https, undici's fetch, database drivers) is checked: loopback, `localhost`
 * and Unix sockets pass; any other address is a dial, recorded with the running test and the stack.
 * `TEST_NETWORK_GUARD`: `fail` (default) refuses the dial with ECONNREFUSED and fails the test in a root
 * `afterEach`, even when the code under test swallowed the refusal; `report` lets it through and only records;
 * `off` installs nothing. `TEST_NETWORK_GUARD_REPORT` names a JSON-lines file every dial is appended to.
 */
import * as fs from 'fs';
import * as net from 'net';

export type GuardMode = 'fail' | 'report';

export interface Dial {
  test: string | null;
  address: string;
  stack: string;
}

export const OPT_IN_TAG = '@network';

const LOCAL_NAMES = new Set(['localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback']);

export function isLocal(host: string | undefined): boolean {
  if (host === undefined || host === '') return true;
  const name = host.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (LOCAL_NAMES.has(name) || name.endsWith('.localhost')) return true;
  if (net.isIPv4(name)) return name.startsWith('127.');
  if (net.isIPv6(name)) {
    if (name === '::1') return true;
    const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(name);
    return mapped !== null && mapped[1]!.startsWith('127.');
  }
  return false;
}

interface ConnectOptions {
  host?: string;
  port?: number | string;
  path?: string;
}

type NormalizeArgs = (args: unknown[]) => [ConnectOptions, unknown];

export class NetworkGuard {
  private pending: Dial[] = [];
  private test: string | null = null;
  private allowed = false;
  private realConnect: typeof net.Socket.prototype.connect | null = null;

  constructor(
    readonly mode: GuardMode,
    private readonly reportPath: string | null = null,
  ) {}

  static fromEnv(env: NodeJS.ProcessEnv = process.env): NetworkGuard | null {
    const mode = (env.TEST_NETWORK_GUARD ?? 'fail').trim().toLowerCase();
    if (mode === 'off') return null;
    if (mode !== 'fail' && mode !== 'report') {
      throw new Error(`TEST_NETWORK_GUARD must be fail, report or off, not ${mode}`);
    }
    return new NetworkGuard(mode, env.TEST_NETWORK_GUARD_REPORT || null);
  }

  install(): void {
    const realConnect = (this.realConnect = net.Socket.prototype.connect);
    const normalize = (net as unknown as { _normalizeArgs: NormalizeArgs })._normalizeArgs;
    const admit = (options: ConnectOptions): boolean => this.admit(options);
    net.Socket.prototype.connect = function guardedConnect(this: net.Socket, ...args: unknown[]) {
      const [options] = normalize(args);
      if (options.path !== undefined || admit(options)) {
        return (realConnect as (...a: unknown[]) => net.Socket).apply(this, args);
      }
      const err = Object.assign(new Error(`unit tests do not reach the network: ${options.host}:${options.port}`), {
        code: 'ECONNREFUSED',
        errno: -111,
        syscall: 'connect',
        address: options.host,
        port: options.port,
      });
      process.nextTick(() => this.destroy(err));
      return this;
    } as typeof net.Socket.prototype.connect;
  }

  uninstall(): void {
    if (this.realConnect !== null) {
      net.Socket.prototype.connect = this.realConnect;
      this.realConnect = null;
    }
  }

  enter(test: string): void {
    this.test = test;
    this.allowed = test.includes(OPT_IN_TAG);
  }

  /** The dials since the last call; the caller fails the test with them in `fail` mode. */
  leave(): Dial[] {
    this.test = null;
    this.allowed = false;
    return this.drain();
  }

  drain(): Dial[] {
    const dials = this.pending;
    this.pending = [];
    return dials;
  }

  private admit(options: ConnectOptions): boolean {
    if (isLocal(options.host) || this.allowed) return true;
    const dial: Dial = {
      test: this.test,
      address: `${options.host}:${options.port}`,
      stack: (new Error().stack ?? '').split('\n').slice(3).join('\n'),
    };
    this.pending.push(dial);
    if (this.reportPath !== null) {
      fs.appendFileSync(this.reportPath, `${JSON.stringify({ mode: this.mode, pid: process.pid, ...dial })}\n`);
    }
    return this.mode === 'report';
  }
}

export function describeDials(dials: Dial[]): string {
  return dials
    .map((d) => `dial to ${d.address} during ${d.test ?? 'module load or a timer left running'}\n${d.stack}`)
    .join('\n');
}
