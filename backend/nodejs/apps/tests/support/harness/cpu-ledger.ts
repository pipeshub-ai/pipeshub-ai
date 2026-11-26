import { NextFunction, Request, Response } from 'express';
import { AsyncHook, AsyncLocalStorage, createHook } from 'node:async_hooks';
import { createHash } from 'node:crypto';
import { BSON } from 'mongodb';
import { readFileSync } from 'node:fs';

/** The main thread's on-CPU time as the scheduler counts it (Linux); elsewhere the process's. */
function loopCpuUs(fallback: number): number {
  try {
    const ns = readFileSync(`/proc/self/task/${String(process.pid)}/schedstat`, 'utf8').split(' ')[0];
    return Number(ns) / 1000;
  } catch {
    return fallback;
  }
}

function processCpuUs(): number {
  const { user, system } = process.cpuUsage();
  return user + system;
}

interface Reading {
  cpuUs: number;
  loopUs: number;
  at: number;
}

const read = (): Reading => {
  const cpuUs = processCpuUs();
  return { cpuUs, loopUs: loopCpuUs(cpuUs), at: performance.now() };
};

export interface CpuLedgerSnapshot {
  /** Since the last reset: every thread's CPU, the event loop thread's alone, and the wall time. */
  cpuUs: number;
  loopUs: number;
  wallMs: number;
  requests: Record<string, number>;
  /** `<kind> | <command> <collection>`: store commands by the request that sent them. */
  commands: Record<string, number>;
  /** Time the event loop ran callbacks of each request kind (`attribute` only). */
  tickUs: Record<string, number>;
}

/** What one request, or everything outside any request, has been charged. */
interface Account {
  kind: string;
  /** Held here until the request ends and its outcome names it; then charged to the totals as it comes. */
  open: boolean;
  us: number;
  commands: Map<string, number>;
}

export interface RequestNaming {
  /** Names a request once its body is parsed; null: not a request to account for. */
  kind(req: Request): string | null;
  /** Its answer is a stream that outlives it: what follows the first frame is `<kind>:stream`. */
  streams?(kind: string): boolean;
  /** A suffix for the kind once the answer's status is known (`claim` that found nothing is not a claim). */
  outcome?(kind: string, status: number): string | null;
}

/**
 * PERF-D: what a request costs this process, by request kind.
 *
 * Always: the process's CPU over a window and how many requests of each kind
 * arrived in it. With `attribute`: the time of every callback the event loop
 * ran, charged to the request whose async context it ran in (a request's own
 * work, the stream it opened, or `background`). Requests overlap on the loop,
 * so only the async context says whose a callback is; the hooks slow promises
 * down, so the shares are applied to a total measured without them.
 */
export class CpuLedger {
  private readonly request = new AsyncLocalStorage<Account>();
  private readonly requests = new Map<string, number>();
  private readonly commands = new Map<string, number>();
  private readonly tickUs = new Map<string, number>();
  private readonly background: Account = { kind: 'background', open: false, us: 0, commands: new Map() };
  private readonly running: Array<{ account: Account; startedNs: bigint; innerNs: bigint }> = [];
  private static readonly NONE = BigInt(0);
  /** A request is named in the callback that parsed it, which began in its socket's context. */
  private arrived: Account | null = null;
  private since = read();
  private readonly hook: AsyncHook | null;

  constructor(attribute = false) {
    this.hook = attribute ? this.callbackTimes().enable() : null;
  }

  /** Stops timing callbacks (a process that outlives its ledger, as a test's does). */
  close(): void {
    this.hook?.disable();
  }

  private callbackTimes(): AsyncHook {
    return createHook({
      before: () => {
        this.running.push({
          account: this.request.getStore() ?? this.background,
          startedNs: process.hrtime.bigint(),
          innerNs: CpuLedger.NONE,
        });
      },
      after: () => {
        const tick = this.running.pop();
        if (tick === undefined) return;
        const ns = process.hrtime.bigint() - tick.startedNs;
        const outer = this.running[this.running.length - 1];
        if (outer !== undefined) outer.innerNs += ns;
        const account = outer === undefined && this.arrived !== null ? this.arrived : tick.account;
        if (outer === undefined) this.arrived = null;
        const us = Number(ns - tick.innerNs) / 1000;
        if (account.open) account.us += us;
        else this.tickUs.set(account.kind, (this.tickUs.get(account.kind) ?? 0) + us);
      },
    });
  }

  /** Counts a store command against the request that sent it (the store client must monitor its commands). */
  command(name: string, collection: string): void {
    const account = this.request.getStore() ?? this.background;
    const op = `${name} ${collection}`;
    if (account.open) account.commands.set(op, (account.commands.get(op) ?? 0) + 1);
    else this.count(this.commands, `${account.kind} | ${op}`, 1);
  }

  private count(into: Map<string, number>, key: string, by: number): void {
    into.set(key, (into.get(key) ?? 0) + by);
  }

  /** Charges what `account` held to the totals under `kind`; what comes later is charged as `later`. */
  private settle(account: Account, kind: string, later: string): void {
    this.count(this.requests, kind, 1);
    if (account.us > 0) this.count(this.tickUs, kind, account.us);
    for (const [op, n] of account.commands) this.count(this.commands, `${kind} | ${op}`, n);
    account.us = 0;
    account.commands.clear();
    account.open = false;
    account.kind = later;
  }

  middleware(naming: RequestNaming) {
    return (req: Request, res: Response, next: NextFunction): void => {
      const kind = naming.kind(req);
      if (kind === null) {
        next();
        return;
      }
      const account: Account = { kind, open: true, us: 0, commands: new Map() };
      this.arrived = account;
      if (naming.streams?.(kind) === true) {
        // The first frame ends the request's own work; what follows is the stream's.
        const write = res.write.bind(res);
        res.write = ((...args: Parameters<Response['write']>) => {
          const written = write(...args);
          if (account.open) this.settle(account, kind, `${kind}:stream`);
          return written;
        }) as Response['write'];
      }
      res.once('close', () => {
        if (!account.open) return;
        const outcome = naming.outcome?.(kind, res.statusCode) ?? null;
        const named = outcome === null ? kind : `${kind}:${outcome}`;
        this.settle(account, named, named);
      });
      this.request.run(account, next);
    };
  }

  snapshot(): CpuLedgerSnapshot {
    const now = read();
    return {
      cpuUs: now.cpuUs - this.since.cpuUs,
      loopUs: now.loopUs - this.since.loopUs,
      wallMs: now.at - this.since.at,
      requests: Object.fromEntries(this.requests),
      commands: Object.fromEntries(this.commands),
      tickUs: Object.fromEntries(this.tickUs),
    };
  }

  reset(): void {
    this.requests.clear();
    this.commands.clear();
    this.tickUs.clear();
    this.since = read();
  }
}

const CALIBRATION_DOCUMENT = {
  orgId: '66f6a6a6a6a6a6a6a6a6a6a6',
  runId: '0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b',
  status: 'running',
  revision: 7,
  updatedAt: new Date(0),
  labels: { sensitivity: 'internal', sources: ['kb:1', 'kb:2', 'web'] },
  events: Array.from({ length: 8 }, (_, seq) => ({
    seq,
    type: 'tool.result',
    critical: false,
    data: { text: 'x'.repeat(256), n: seq },
  })),
};

/**
 * A fixed piece of the work a request is made of (BSON and JSON both ways, a
 * digest), timed on the event loop's thread. The same instructions take longer
 * on a contended host; a request's CPU divided by this is what a loaded host
 * leaves comparable.
 */
export function calibrationUs(rounds = 200): number {
  const before = loopCpuUs(processCpuUs());
  let bytes = 0;
  for (let i = 0; i < rounds; i += 1) {
    const wire = BSON.serialize(CALIBRATION_DOCUMENT);
    const back = BSON.deserialize(wire);
    const text = JSON.stringify(back);
    bytes += createHash('sha256').update(text).digest().length + (JSON.parse(text) as { events: unknown[] }).events.length;
  }
  return bytes > 0 ? loopCpuUs(processCpuUs()) - before : 0;
}

/** A claim that claimed nothing is not what a claim costs. */
function harnessRequestOutcome(kind: string, status: number): string | null {
  if (!/\bclaim$/.test(kind)) return null;
  if (status === 200) return 'claimed';
  return status === 204 ? 'empty' : 'refused';
}

/** The harness requests the CPU bench reports. */
export const harnessRequests: RequestNaming = {
  kind: harnessRequestKind,
  streams: (kind) => kind === 'admission',
  outcome: harnessRequestOutcome,
};

/** A commit is named by the transition it carries; ids are not part of a name. */
export function harnessRequestKind(req: Request): string | null {
  const path = req.path;
  if (path.startsWith('/__fault/')) return null;
  if (req.method === 'POST' && /\/runs\/[^/]+\/commit$/.test(path)) {
    const transition = (req.body as { transition?: { status?: string } } | undefined)?.transition;
    return `commit:${transition?.status ?? 'step'}`;
  }
  if (req.method === 'POST' && path.endsWith('/stream')) return 'admission';
  const internal = /\/internal\/(.*)$/.exec(path);
  const tail = (internal?.[1] ?? path).replace(/[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{24}/g, ':id');
  return `${req.method} ${tail}`;
}
