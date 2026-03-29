import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { smtpConfigCheck } from '../../../../src/modules/user_management/middlewares/smtpConfigCheck';
import { ConfigurationManagerServiceCommand } from '../../../../src/libs/commands/configuration_manager/cm.service.command';

describe('smtpConfigCheck Middleware', () => {
  let req: any;
  let res: any;
  let next: sinon.SinonStub;
  let executeStub: sinon.SinonStub;
  const cmBackend = 'http://localhost:3004';

  beforeEach(() => {
    req = {
      user: {
        orgId: '507f1f77bcf86cd799439012',
      },
      headers: {
        authorization: 'Bearer test-token',
      },
    };
    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub(),
    };
    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should return a middleware function when called with cmBackend', () => {
    const middleware = smtpConfigCheck(cmBackend);
    expect(middleware).to.be.a('function');
  });

  it('should call next() when SMTP config is valid', async () => {
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: {
        host: 'smtp.example.com',
        port: 587,
        fromEmail: 'noreply@example.com',
      },
    });

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next with InternalServerError when response statusCode is not 200', async () => {
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').resolves({
      statusCode: 500,
      data: { error: { message: 'Server error' } },
    });

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Error getting smtp config');
  });

  it('should call next with NotFoundError when credentials data is null', async () => {
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: null,
    });

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Smtp Configuration not found');
  });

  it('should call next with NotFoundError when host is missing', async () => {
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: {
        port: 587,
        fromEmail: 'noreply@example.com',
      },
    });

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Smtp Host is missing');
  });

  it('should call next with NotFoundError when port is missing', async () => {
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: {
        host: 'smtp.example.com',
        fromEmail: 'noreply@example.com',
      },
    });

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Smtp Port is missing');
  });

  it('should call next with NotFoundError when fromEmail is missing', async () => {
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: {
        host: 'smtp.example.com',
        port: 587,
      },
    });

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Smtp From Email is missing');
  });

  it('should handle errors from the command execution', async () => {
    const commandError = new Error('Network error');
    executeStub = sinon.stub(ConfigurationManagerServiceCommand.prototype, 'execute').rejects(commandError);

    const middleware = smtpConfigCheck(cmBackend);
    await middleware(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Network error');
  });
});
