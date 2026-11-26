/** PERF-D: the CPU bench's ledger says whose a callback was, a command was, and what a request is called. */
import { expect } from 'chai';
import { EventEmitter } from 'node:events';
import { Request, Response } from 'express';
import { calibrationUs, CpuLedger, harnessRequestKind, harnessRequests } from './cpu-ledger';

const request = (method: string, path: string, body: unknown = {}): Request => ({ method, path, body }) as Request;

const response = (): Response => {
  const res = new EventEmitter() as unknown as Response;
  res.write = (() => true) as Response['write'];
  res.statusCode = 200;
  return res;
};

const turn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

/** Work the loop can only do on its own thread, in an amount the caller chooses. */
function work(rounds: number): number {
  let sum = 0;
  for (let i = 0; i < rounds * 20_000; i += 1) sum += i % 7;
  return sum;
}

const named = (req: Request): string | null => (req.path === '/skip' ? null : req.path.slice(1));

describe('PERF-D: CpuLedger', () => {
  let ledger: CpuLedger;

  afterEach(() => {
    ledger.close();
  });

  /** Runs `handler` as the route behind the ledger's middleware, ends the answer and waits for it. */
  const serve = (path: string, handler: (res: Response) => Promise<void>, streams = false): Promise<void> =>
    new Promise((resolve, reject) => {
      const res = response();
      const naming = {
        kind: named,
        streams: () => streams,
        outcome: (_kind: string, status: number) => (status === 204 ? 'empty' : null),
      };
      ledger.middleware(naming)(request('POST', path), res, () => {
        handler(res)
          .then(() => {
            res.emit('close');
          })
          .then(resolve, reject);
      });
    });

  it('PERFD-1: a callback is charged to the request whose async context it ran in, though requests overlap', async () => {
    ledger = new CpuLedger(true);
    // Once through everything first: the first call of a function is not what it costs.
    await serve('/light', async () => {
      await turn();
      work(1);
    });
    await turn();
    ledger.reset();

    const heavy = serve('/heavy', async () => {
      for (let i = 0; i < 5; i += 1) {
        await turn();
        work(40);
      }
    });
    // A request arrives in a callback of its own.
    await turn();
    const light = serve('/light', async () => {
      for (let i = 0; i < 5; i += 1) {
        await turn();
        work(1);
      }
    });
    await Promise.all([heavy, light]);
    await turn();
    work(40);
    await turn();

    const { tickUs, requests } = ledger.snapshot();
    expect(requests).to.deep.equal({ heavy: 1, light: 1 });
    expect(tickUs['light']).to.be.greaterThan(0);
    expect(tickUs['heavy']).to.be.greaterThan(5 * tickUs['light']!);
    expect(tickUs['background'], 'work outside any request').to.be.greaterThan(tickUs['light']!);
  });

  it('PERFD-1: without attribution no callback is timed, and the process totals are still read', async () => {
    ledger = new CpuLedger();
    await serve('/heavy', async () => {
      await turn();
      work(40);
    });
    const snapshot = ledger.snapshot();
    expect(snapshot.tickUs).to.deep.equal({});
    expect(snapshot.requests).to.deep.equal({ heavy: 1 });
    expect(snapshot.loopUs).to.be.greaterThan(0);
    expect(snapshot.cpuUs).to.be.greaterThan(0);
  });

  it('PERFD-2: what a stream does after its first frame is the stream\'s, not the request\'s', async () => {
    ledger = new CpuLedger(true);
    await serve(
      '/admission',
      async (res) => {
        await turn();
        work(5);
        res.write('event: created\n\n');
        await turn();
        work(5);
      },
      true,
    );
    const { tickUs } = ledger.snapshot();
    expect(tickUs['admission']).to.be.greaterThan(0);
    expect(tickUs['admission:stream']).to.be.greaterThan(0);
  });

  it('PERFD-3: a store command is counted for the request that sent it, from any of its continuations', async () => {
    ledger = new CpuLedger();
    const first = serve('/commit', async () => {
      ledger.command('find', 'agentRuns');
      await turn();
      ledger.command('find', 'agentRuns');
      ledger.command('insert', 'sessionEvents');
    });
    const second = serve('/claim', async () => {
      await turn();
      ledger.command('find', 'agentRuns');
    });
    await Promise.all([first, second]);
    ledger.command('find', 'outbox_events');

    expect(ledger.snapshot().commands).to.deep.equal({
      'commit | find agentRuns': 2,
      'commit | insert sessionEvents': 1,
      'claim | find agentRuns': 1,
      'background | find outbox_events': 1,
    });
  });

  it('PERFD-4: a request is named with its outcome once it has one, and its callbacks and commands with it', async () => {
    ledger = new CpuLedger(true);
    await serve('/claim', async (res) => {
      await turn();
      work(2);
      ledger.command('find', 'agentRuns');
      res.statusCode = 204;
    });
    await serve('/claim', async () => {
      await turn();
      ledger.command('findAndModify', 'agentRuns');
    });
    const { requests, commands, tickUs } = ledger.snapshot();
    expect(requests).to.deep.equal({ 'claim:empty': 1, claim: 1 });
    expect(commands).to.deep.equal({ 'claim:empty | find agentRuns': 1, 'claim | findAndModify agentRuns': 1 });
    expect(tickUs['claim:empty']).to.be.greaterThan(0);
  });

  it('PERFD-4: the harness names a claim by whether it claimed', () => {
    const outcome = harnessRequests.outcome!;
    expect(outcome('POST runs/claim', 200)).to.equal('claimed');
    expect(outcome('POST runs/claim', 204)).to.equal('empty');
    expect(outcome('POST runs/:id/claim', 409)).to.equal('refused');
    expect(outcome('commit:step', 200)).to.equal(null);
    expect(harnessRequests.streams!('admission')).to.equal(true);
    expect(harnessRequests.streams!('commit:step')).to.equal(false);
  });

  it('PERFD-3: a reset starts the window again', async () => {
    ledger = new CpuLedger(true);
    await serve('/commit', async () => {
      ledger.command('find', 'agentRuns');
      await turn();
    });
    ledger.reset();
    const snapshot = ledger.snapshot();
    expect(snapshot.requests).to.deep.equal({});
    expect(snapshot.commands).to.deep.equal({});
  });

  it('PERFD-4: a request the namer does not name is not counted', async () => {
    ledger = new CpuLedger();
    await serve('/skip', async () => turn());
    expect(ledger.snapshot().requests).to.deep.equal({});
  });

  it('PERFD-4: a request is named by what it is, never by an id', () => {
    const internal = '/api/v1/agent-harness/internal';
    const run = '0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b';
    const cases: Array<[Request, string | null]> = [
      [request('POST', `${internal}/runs/${run}/commit`, { events: [] }), 'commit:step'],
      [request('POST', `${internal}/runs/${run}/commit`, { transition: { status: 'completed' } }), 'commit:completed'],
      [request('POST', `${internal}/runs/${run}/commit`, { transition: { status: 'suspended' } }), 'commit:suspended'],
      [request('POST', `${internal}/runs/claim`), 'POST runs/claim'],
      [request('POST', `${internal}/runs/${run}/claim`), 'POST runs/:id/claim'],
      [request('GET', `${internal}/runs/${run}/events`), 'GET runs/:id/events'],
      [request('POST', '/api/v1/agents/assistant/conversations/stream'), 'admission'],
      [request('POST', '/api/v1/conversations/66f6a6a6a6a6a6a6a6a6a6a6/messages/stream'), 'admission'],
      [request('GET', '/__fault/cpu'), null],
    ];
    for (const [req, kind] of cases) expect(harnessRequestKind(req), `${req.method} ${req.path}`).to.equal(kind);
  });

  it('PERFD-5: the calibration unit is the same work every time, timed on the loop\'s thread', () => {
    ledger = new CpuLedger();
    expect(calibrationUs(20)).to.be.greaterThan(0);
    expect(calibrationUs(200)).to.be.greaterThan(calibrationUs(2));
  });
});
