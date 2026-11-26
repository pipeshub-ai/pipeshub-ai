import { expect } from 'chai';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { Writable } from 'stream';
import {
  DEFAULT_MAX_BUFFERED_BYTES,
  FileSink,
  SinkReport,
} from '../../../src/libs/services/log-file-sink';

const MESSAGE = Symbol.for('message');

function line(text: string): Record<string | symbol, unknown> {
  return { level: 'info', message: text, [MESSAGE]: text };
}

// A file that never drains: what a stalled disk or a full pipe looks like to the sink.
class StuckStream extends Writable {
  readonly pending: Array<() => void> = [];

  constructor() {
    super({ highWaterMark: 64 });
  }

  override _write(_chunk: Buffer, _enc: BufferEncoding, done: () => void): void {
    this.pending.push(done);
  }

  release(): void {
    while (this.pending.length) (this.pending.shift() as () => void)();
  }
}

function logAll(sink: FileSink, count: number, prefix = 'line'): number {
  let completed = 0;
  for (let i = 0; i < count; i += 1) {
    sink.log(line(`${prefix} ${i}`), () => {
      completed += 1;
    });
  }
  return completed;
}

const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

describe('FileSink (D3)', () => {
  let dir: string;
  let reports: SinkReport[];
  const report = (r: SinkReport) => reports.push(r);

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'd3-sink-'));
    reports = [];
  });

  afterEach(() => {
    fs.chmodSync(dir, 0o700);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it('LS-4: appends every line, in order, one per line', async () => {
    const filePath = path.join(dir, 'nested', 'combined.log');
    const sink = new FileSink({ sink: 'combined', filePath, report });
    expect(logAll(sink, 500)).to.equal(500);
    await new Promise<void>((resolve) => {
      sink.close();
      (sink as any).stream.once('finish', resolve);
    });
    const lines = fs.readFileSync(filePath, 'utf8').trimEnd().split('\n');
    expect(lines).to.have.length(500);
    expect(lines[0]).to.equal('line 0');
    expect(lines[499]).to.equal('line 499');
    expect(reports).to.deep.equal([]);
    expect(sink.healthy).to.equal(true);
  });

  it('LS-1: a directory that cannot be created fails the sink once, and log never waits', async () => {
    const blocker = path.join(dir, 'a-file');
    fs.writeFileSync(blocker, '');
    const sink = new FileSink({ sink: 'combined', filePath: path.join(blocker, 'x', 'combined.log'), report });
    expect(logAll(sink, 2000)).to.equal(2000);
    await tick();
    expect(sink.healthy).to.equal(false);
    expect(reports).to.have.length(1);
    expect(reports[0]).to.include({ event: 'logger.sink_failed', sink: 'combined' });
    expect(reports[0]!.errorCode).to.be.oneOf(['ENOTDIR', 'EEXIST']);
  });

  it('LS-1: a file that cannot be opened fails the sink once, after which lines are not held', async function () {
    if (process.getuid?.() === 0) this.skip(); // root ignores the mode bits
    fs.chmodSync(dir, 0o500);
    const sink = new FileSink({ sink: 'error', filePath: path.join(dir, 'error.log'), report });
    expect(logAll(sink, 2000)).to.equal(2000);
    // The open fails on libuv's thread pool, which a loaded host can hold for longer than any fixed
    // number of event-loop turns: wait on time, bounded, not on turns.
    const deadline = Date.now() + 5000;
    while (sink.healthy && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 5));
    expect(sink.healthy).to.equal(false);
    expect((sink as any).stream).to.equal(null);
    expect(logAll(sink, 2000, 'after')).to.equal(2000);
    expect(reports).to.have.length(1);
    expect(reports[0]).to.deep.include({ event: 'logger.sink_failed', sink: 'error', errorCode: 'EACCES' });
  });

  it('LS-2: a stream that errors after opening is dropped and reported once', async () => {
    const stream = new StuckStream();
    const sink = new FileSink({ sink: 'combined', filePath: '/virtual/combined.log', report, openStream: () => stream });
    logAll(sink, 10);
    stream.destroy(Object.assign(new Error('disk gone'), { code: 'EIO' }));
    await tick();
    expect(logAll(sink, 100)).to.equal(100);
    stream.emit('error', Object.assign(new Error('again'), { code: 'EIO' }));
    expect(reports).to.deep.equal([
      { event: 'logger.sink_failed', sink: 'combined', path: '/virtual/combined.log', errorCode: 'EIO' },
    ]);
  });

  it('LS-3: a file that never drains caps what it holds, counts the drops, and resumes after the drain', async () => {
    const stream = new StuckStream();
    const maxBufferedBytes = 1024;
    const sink = new FileSink({
      sink: 'combined',
      filePath: '/virtual/combined.log',
      report,
      maxBufferedBytes,
      openStream: () => stream,
    });
    expect(logAll(sink, 2000)).to.equal(2000);
    expect(stream.writableLength).to.be.at.most(maxBufferedBytes);
    expect(reports).to.deep.equal([
      { event: 'logger.sink_overflow', sink: 'combined', path: '/virtual/combined.log' },
    ]);

    stream.release();
    await tick();
    stream.release();
    expect(stream.writableLength).to.equal(0);
    expect(logAll(sink, 1, 'resumed')).to.equal(1);
    expect(reports).to.have.length(2);
    expect(reports[1]).to.include({ event: 'logger.sink_recovered', sink: 'combined' });
    expect(reports[1]!.dropped).to.be.greaterThan(1800);
    expect(stream.writableLength).to.be.greaterThan(0);
    expect(sink.healthy).to.equal(true);
  });

  it('holds at most 1 MiB by default', () => {
    expect(DEFAULT_MAX_BUFFERED_BYTES).to.equal(1024 * 1024);
  });

  it('a non-errno failure is reported with errorCode unknown', () => {
    const sink = new FileSink({
      sink: 'combined',
      filePath: '/virtual/combined.log',
      report,
      openStream: () => {
        throw new Error('no code');
      },
    });
    expect(sink.healthy).to.equal(false);
    expect(reports[0]!.errorCode).to.equal('unknown');
    sink.close();
  });
});
