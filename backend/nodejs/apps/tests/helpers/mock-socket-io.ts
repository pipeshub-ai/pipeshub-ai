import sinon from 'sinon';

export interface MockSocketIO {
  to: sinon.SinonStub;
  emit: sinon.SinonStub;
  on: sinon.SinonStub;
  close: sinon.SinonStub;
  sockets: {
    adapter: {
      rooms: Map<string, Set<string>>;
    };
  };
}

export function createMockSocketIO(): MockSocketIO {
  const io: MockSocketIO = {
    to: sinon.stub(),
    emit: sinon.stub(),
    on: sinon.stub(),
    close: sinon.stub(),
    sockets: {
      adapter: {
        rooms: new Map(),
      },
    },
  };
  // Chain: io.to(room).emit(event, data)
  io.to.returns({ emit: io.emit });
  return io;
}
