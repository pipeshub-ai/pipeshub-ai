import 'reflect-metadata';
import sinon from 'sinon';

process.env.NODE_ENV = 'test';
process.env.LOG_LEVEL = 'error';
process.env.CONNECTOR_BACKEND = 'http://connector.local';

afterEach(() => {
  sinon.restore();
});
