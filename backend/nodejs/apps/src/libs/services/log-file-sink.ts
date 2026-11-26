import fs from 'fs';
import path from 'path';
import { Writable } from 'stream';
import winston from 'winston';
// Types only: winston's own typings import this package, so it is present wherever winston is.
import type TransportStream from 'winston-transport';

const MESSAGE = Symbol.for('message');

// winston's typings call the transport base class `transport`; at runtime it is `Transport`.
const TransportBase = (
  winston as unknown as { Transport: typeof TransportStream }
).Transport;

export const DEFAULT_MAX_BUFFERED_BYTES = 1024 * 1024;

export type SinkEvent =
  'logger.sink_failed' | 'logger.sink_overflow' | 'logger.sink_recovered';

export interface SinkReport {
  event: SinkEvent;
  sink: string;
  path: string;
  errorCode?: string;
  dropped?: number;
}

export type SinkReporter = (report: SinkReport) => void;

export interface FileSinkOptions
  extends TransportStream.TransportStreamOptions {
  sink: string;
  filePath: string;
  report: SinkReporter;
  maxBufferedBytes?: number;
  openStream?: (filePath: string) => Writable;
}

function appendStream(filePath: string): Writable {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  return fs.createWriteStream(filePath, { flags: 'a' });
}

function errorCode(error: unknown): string {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return typeof code === 'string' ? code : 'unknown';
}

/**
 * Appends formatted lines to one file without ever making the logger wait.
 *
 * winston pipes its logger into every transport, and a pipe pauses for the
 * slowest destination: winston's own File transport holds its callback while
 * its file is unopened or full, so one unwritable file silenced the console
 * too (H5). Here `log` always completes at once; a failed file is reported
 * once and dropped, and a file that falls behind holds at most
 * `maxBufferedBytes` before lines are dropped and counted.
 */
export class FileSink extends TransportBase {
  private readonly sink: string;
  private readonly filePath: string;
  private readonly report: SinkReporter;
  private readonly maxBufferedBytes: number;
  private stream: Writable | null = null;
  private failed = false;
  private overflowing = false;
  private dropped = 0;

  constructor(options: FileSinkOptions) {
    super(options);
    this.sink = options.sink;
    this.filePath = options.filePath;
    this.report = options.report;
    this.maxBufferedBytes =
      options.maxBufferedBytes ?? DEFAULT_MAX_BUFFERED_BYTES;
    try {
      const stream = (options.openStream ?? appendStream)(this.filePath);
      stream.on('error', (error) => {
        this.fail(error);
      });
      this.stream = stream;
    } catch (error) {
      this.fail(error);
    }
  }

  get healthy(): boolean {
    return !this.failed;
  }

  override log(
    info: Record<string | symbol, unknown>,
    callback: () => void,
  ): void {
    if (this.stream && !this.failed) {
      this.append(`${String(info[MESSAGE] ?? info.message)}\n`);
    }
    callback();
  }

  override close(): void {
    this.stream?.end();
  }

  private append(line: string): void {
    const stream = this.stream as Writable;
    if (
      stream.writableLength + Buffer.byteLength(line) >
      this.maxBufferedBytes
    ) {
      this.dropped += 1;
      if (!this.overflowing) {
        this.overflowing = true;
        this.report({
          event: 'logger.sink_overflow',
          sink: this.sink,
          path: this.filePath,
        });
      }
      return;
    }
    if (this.overflowing) {
      this.report({
        event: 'logger.sink_recovered',
        sink: this.sink,
        path: this.filePath,
        dropped: this.dropped,
      });
      this.overflowing = false;
      this.dropped = 0;
    }
    stream.write(line);
  }

  private fail(error: unknown): void {
    if (this.failed) return;
    this.failed = true;
    this.stream?.destroy();
    this.stream = null;
    this.report({
      event: 'logger.sink_failed',
      sink: this.sink,
      path: this.filePath,
      errorCode: errorCode(error),
    });
  }
}
