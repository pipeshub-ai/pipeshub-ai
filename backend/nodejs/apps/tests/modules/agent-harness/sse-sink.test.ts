/**
 * A durable stream's HTTP side: the SSE head, and the sink the stream
 * service writes frames to (flushed per write, backpressure read from the
 * response's buffer, ended once).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'node:events';
import { responseSink, writeSseHead } from '../../../src/modules/agent-harness/transport/sse';

const fakeResponse = () => {
  const res = Object.assign(new EventEmitter(), {
    writeHead: sinon.spy(),
    write: sinon.stub().returns(true),
    flush: sinon.spy(),
    end: sinon.spy(function (this: { writableEnded: boolean }) {
      this.writableEnded = true;
    }),
    writableEnded: false,
    writableLength: 0,
  });
  return res;
};

describe('durable stream SSE transport', () => {
  it('writes an event-stream head that proxies do not buffer, with no CORS header of its own', () => {
    const res = fakeResponse();
    writeSseHead(res as never);
    const [status, headers] = res.writeHead.firstCall.args as [number, Record<string, string>];
    expect(status).to.equal(200);
    expect(headers).to.deep.equal({
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
  });

  it("flushes each frame and reports the response's backpressure", () => {
    const res = fakeResponse();
    const sink = responseSink(res as never);
    res.write.returns(false);
    expect(sink.write('data: 1\n\n')).to.equal(false);
    expect(res.flush.calledOnce).to.equal(true);
    res.writableLength = 4096;
    expect(sink.pending).to.equal(4096);
    const drained = sinon.spy();
    sink.onDrain(drained);
    res.emit('drain');
    expect(drained.calledOnce).to.equal(true);
  });

  it('ends the response once however often the stream ends', () => {
    const res = fakeResponse();
    const sink = responseSink(res as never);
    sink.end();
    sink.end();
    expect(res.end.calledOnce).to.equal(true);
  });
});
