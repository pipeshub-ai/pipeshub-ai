import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { MailProducer } from '../../../../src/modules/mail/services/mail.producer';
import { BrokerTopic } from '../../../../src/libs/types/messaging.types';
import { MailEventType } from '../../../../src/modules/mail/types/mail-event.types';

describe('MailProducer - publishing mail jobs', () => {
  let connected: boolean;
  let broker: {
    isConnected: sinon.SinonStub;
    connect: sinon.SinonStub;
    disconnect: sinon.SinonStub;
    publish: sinon.SinonStub;
  };
  let producer: MailProducer;

  const event = (orgId?: string) => ({
    eventType: MailEventType.SendMailEvent,
    timestamp: 1_700_000_000_000,
    payload: {
      mail: { emailTemplateType: 'appuserInvite', subject: 'Hi', sendEmailTo: ['u@example.com'] },
      ...(orgId !== undefined ? { orgId } : {}),
    },
  });

  beforeEach(() => {
    connected = false;
    broker = {
      isConnected: sinon.stub().callsFake(() => connected),
      connect: sinon.stub().callsFake(async () => {
        connected = true;
      }),
      disconnect: sinon.stub().callsFake(async () => {
        connected = false;
      }),
      publish: sinon.stub().resolves(),
    };
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() };
    producer = new MailProducer(broker as never, logger as never);
  });

  it('connects once on start and disconnects only when connected', async () => {
    await producer.start();
    await producer.start();
    expect(broker.connect.callCount).to.equal(1);
    expect(producer.isConnected()).to.equal(true);
    await producer.stop();
    await producer.stop();
    expect(broker.disconnect.callCount).to.equal(1);
    expect(producer.isConnected()).to.equal(false);
  });

  it('reconnects a producer another module stopped, and keys the job by org', async () => {
    await producer.publishEvent(event('org-1') as never);
    expect(broker.connect.callCount).to.equal(1);
    const [topic, message] = broker.publish.firstCall.args;
    expect(topic).to.equal(BrokerTopic.MAIL_EVENTS);
    expect(message.key).to.equal('org-1');
    expect(message.headers).to.deep.equal({ eventType: 'sendMail', timestamp: '1700000000000' });
  });

  it('keys a job without an org by its template, and rethrows a failed publish', async () => {
    broker.publish.rejects(new Error('broker down'));
    let caught: unknown;
    try {
      await producer.publishEvent(event() as never);
    } catch (error) {
      caught = error;
    }
    expect((caught as Error).message).to.equal('broker down');
    expect(broker.publish.firstCall.args[1].key).to.equal('appuserInvite');
  });
});
