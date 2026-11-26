import { expect } from 'chai';
import http from 'http';
import { AddressInfo } from 'net';
import { trackRequests } from '../../../src/libs/services/http-drain';

interface Answer {
  status: number;
  body: string;
}

/** A request whose answer the test releases, an event stream, and a request that is never answered. */
const serve = async () => {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const arrived = new Map<string, () => void>();
  const seen = (path: string): Promise<void> =>
    new Promise((resolve) => {
      arrived.set(path, resolve);
    });
  const server = http.createServer((request, response) => {
    arrived.get(request.url ?? '')?.();
    if (request.url === '/held') {
      void held.then(() => response.end('answered'));
    } else if (request.url === '/stream') {
      response.writeHead(200, { 'content-type': 'text/event-stream' });
      response.write('data: 1\n\n');
    } else if (request.url === '/stream-with-stored-headers') {
      response.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
      response.write('data: 1\n\n');
    } else if (request.url === '/becomes-a-stream') {
      void held.then(() => {
        response.writeHead(200, { 'content-type': 'text/event-stream' });
        response.write('data: 1\n\n');
      });
    }
  });
  const drain = trackRequests(server);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const get = (path: string): Promise<Answer> =>
    new Promise((resolve, reject) => {
      http
        .get({ host: '127.0.0.1', port, path, agent: false }, (response) => {
          let body = '';
          response.on('data', (chunk: Buffer) => (body += chunk.toString()));
          response.on('end', () => resolve({ status: response.statusCode ?? 0, body }));
          response.on('error', reject);
        })
        .on('error', reject);
    });
  return { server, drain, get, seen, release };
};

/** What a client saw of a stream the server ended: the data it got, or how the connection broke. */
const streamed = (
  port: (path: string) => Promise<Answer>,
  path: string,
): Promise<string> =>
  port(path).then(
    (answer) => answer.body,
    (error: NodeJS.ErrnoException) => error.code ?? error.message,
  );

// Far longer than any case takes: a drain that ends only at its deadline fails the test's own timeout.
const NEVER_MS = 600_000;

/** Lets the event loop go round, timers included: a drain that did not wait has cut its connections by then. */
const loopTurns = async (turns = 5): Promise<void> => {
  for (let turn = 0; turn < turns; turn += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

describe('http drain (OPS1-1-8)', function () {
  this.timeout(10_000);

  it('answers the request it accepted, then refuses new connections', async () => {
    const { drain, get, seen, release } = await serve();
    const arrived = seen('/held');
    const inFlight = get('/held');
    await arrived;
    const drained = drain.drain(NEVER_MS);
    await loopTurns();
    release();
    expect(await inFlight).to.deep.equal({ status: 200, body: 'answered' });
    await drained;
    expect(await streamed(get, '/held')).to.equal('ECONNREFUSED');
  });

  it('ends an open event stream once the requests are answered, and never waits for the stream', async () => {
    const { drain, get, seen, release } = await serve();
    const streaming = seen('/stream');
    const stream = streamed(get, '/stream');
    const arrived = seen('/held');
    const inFlight = get('/held');
    await Promise.all([streaming, arrived]);
    const drained = drain.drain(NEVER_MS);
    await loopTurns();
    release();
    expect((await inFlight).body).to.equal('answered');
    await drained;
    expect(await stream).to.be.oneOf(['data: 1\n\n', 'ECONNRESET', 'aborted']);
  });

  it('knows an event stream whichever way its headers were set', async () => {
    const { drain, get, seen } = await serve();
    const arrived = seen('/stream-with-stored-headers');
    const stream = streamed(get, '/stream-with-stored-headers');
    await arrived;
    await drain.drain(NEVER_MS);
    expect(await stream).to.be.oneOf(['data: 1\n\n', 'ECONNRESET', 'aborted']);
  });

  it('a request that turns into an event stream after the drain began stops holding it', async () => {
    const { drain, get, seen, release } = await serve();
    const arrived = seen('/becomes-a-stream');
    const stream = streamed(get, '/becomes-a-stream');
    await arrived;
    const drained = drain.drain(NEVER_MS);
    release();
    await drained;
    expect(await stream).to.be.oneOf(['data: 1\n\n', 'ECONNRESET', 'aborted']);
  });

  it('cuts a request that outlives the deadline', async () => {
    const { drain, get, seen } = await serve();
    const arrived = seen('/never');
    const stuck = streamed(get, '/never');
    await arrived;
    await drain.drain(20);
    expect(await stuck).to.be.oneOf(['ECONNRESET', 'socket hang up']);
  });

  it('a server that never listened drains at once', async () => {
    await trackRequests(http.createServer()).drain(NEVER_MS);
  });
});
