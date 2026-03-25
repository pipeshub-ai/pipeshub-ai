import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { attachContainerMiddleware } from '../../../../src/modules/auth/middlewares/attachContainer.middleware';

describe('attachContainerMiddleware', () => {
  afterEach(() => {
    sinon.restore();
  });

  it('should return a middleware function', () => {
    const container = new Container();
    const middleware = attachContainerMiddleware(container);
    expect(middleware).to.be.a('function');
  });

  it('should attach the container to the request object', () => {
    const container = new Container();
    const middleware = attachContainerMiddleware(container);

    const req: any = {};
    const res: any = {};
    const next = sinon.stub();

    middleware(req, res, next);

    expect(req.container).to.equal(container);
  });

  it('should call next() after attaching the container', () => {
    const container = new Container();
    const middleware = attachContainerMiddleware(container);

    const req: any = {};
    const res: any = {};
    const next = sinon.stub();

    middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
  });

  it('should call next() with no arguments (no error)', () => {
    const container = new Container();
    const middleware = attachContainerMiddleware(container);

    const req: any = {};
    const res: any = {};
    const next = sinon.stub();

    middleware(req, res, next);

    expect(next.calledWithExactly()).to.be.true;
  });

  it('should overwrite any existing container on the request', () => {
    const oldContainer = new Container();
    const newContainer = new Container();
    const middleware = attachContainerMiddleware(newContainer);

    const req: any = { container: oldContainer };
    const res: any = {};
    const next = sinon.stub();

    middleware(req, res, next);

    expect(req.container).to.equal(newContainer);
    expect(req.container).to.not.equal(oldContainer);
  });
});
