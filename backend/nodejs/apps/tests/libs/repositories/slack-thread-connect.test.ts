import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';

/** The Slack bot's own MongoDB connection is fenced like the API's (the fence module is stubbed: it would patch the driver for the process). */
describe('Slack bot MongoDB connection', () => {
  afterEach(() => sinon.restore());

  it('installs the store fence and connects with the armed client options', async () => {
    const fence = require('../../../src/libs/repositories/fence') as typeof import('../../../src/libs/repositories/fence');
    const install = sinon.stub(fence, 'installMongoFence');
    sinon.stub(fence, 'fenceClientOptions').returns({ waitQueueTimeoutMS: 30000, appName: fence.FENCED_APP_NAME });
    const connect = sinon.stub(mongoose, 'connect').resolves(mongoose);
    const path = '../../../src/libs/repositories/slack-thread/mongo';
    delete require.cache[require.resolve(path)];
    const { connect: open } = require(path) as typeof import('../../../src/libs/repositories/slack-thread/mongo');
    await open('mongodb://db:27017/');
    expect(install.calledOnceWith(mongoose.mongo.Collection)).to.equal(true);
    expect(connect.firstCall.args[1]).to.include({ appName: 'pipeshub-fenced', waitQueueTimeoutMS: 30000 });
  });
});
