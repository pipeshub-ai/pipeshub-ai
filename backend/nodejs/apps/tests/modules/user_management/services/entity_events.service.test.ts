import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  EntitiesEventProducer,
  EventType,
  SyncAction,
  AccountType,
  Event,
  OrgAddedEvent,
  UserAddedEvent,
  UserDeletedEvent,
  UserUpdatedEvent,
  OrgUpdatedEvent,
  OrgDeletedEvent,
} from '../../../../src/modules/user_management/services/entity_events.service';
import { OutboxEvent } from '../../../../src/libs/services/outbox/outbox.schema';

describe('EntitiesEventProducer', () => {
  describe('Enums', () => {
    describe('AccountType', () => {
      it('should have Individual value', () => {
        expect(AccountType.Individual).to.equal('individual');
      });

      it('should have Business value', () => {
        expect(AccountType.Business).to.equal('business');
      });
    });

    describe('SyncAction', () => {
      it('should have None value', () => {
        expect(SyncAction.None).to.equal('none');
      });

      it('should have Immediate value', () => {
        expect(SyncAction.Immediate).to.equal('immediate');
      });

      it('should have Scheduled value', () => {
        expect(SyncAction.Scheduled).to.equal('scheduled');
      });
    });

    describe('EventType', () => {
      it('should have OrgCreatedEvent value', () => {
        expect(EventType.OrgCreatedEvent).to.equal('orgCreated');
      });

      it('should have OrgUpdatedEvent value', () => {
        expect(EventType.OrgUpdatedEvent).to.equal('orgUpdated');
      });

      it('should have OrgDeletedEvent value', () => {
        expect(EventType.OrgDeletedEvent).to.equal('orgDeleted');
      });

      it('should have NewUserEvent value', () => {
        expect(EventType.NewUserEvent).to.equal('userAdded');
      });

      it('should have UpdateUserEvent value', () => {
        expect(EventType.UpdateUserEvent).to.equal('userUpdated');
      });

      it('should have DeleteUserEvent value', () => {
        expect(EventType.DeleteUserEvent).to.equal('userDeleted');
      });
    });
  });

  describe('Event Interfaces', () => {
    it('should allow constructing an OrgAddedEvent', () => {
      const event: OrgAddedEvent = {
        orgId: 'org123',
        accountType: AccountType.Business,
        registeredName: 'Test Corp',
      };
      expect(event.orgId).to.equal('org123');
      expect(event.accountType).to.equal('business');
      expect(event.registeredName).to.equal('Test Corp');
    });

    it('should allow constructing an OrgUpdatedEvent', () => {
      const event: OrgUpdatedEvent = {
        orgId: 'org123',
        registeredName: 'Updated Corp',
      };
      expect(event.orgId).to.equal('org123');
      expect(event.registeredName).to.equal('Updated Corp');
    });

    it('should allow constructing an OrgDeletedEvent', () => {
      const event: OrgDeletedEvent = {
        orgId: 'org123',
      };
      expect(event.orgId).to.equal('org123');
    });

    it('should allow constructing a UserAddedEvent', () => {
      const event: UserAddedEvent = {
        orgId: 'org123',
        userId: 'user456',
        fullName: 'John Doe',
        email: 'john@test.com',
        syncAction: SyncAction.Immediate,
      };
      expect(event.orgId).to.equal('org123');
      expect(event.userId).to.equal('user456');
      expect(event.email).to.equal('john@test.com');
      expect(event.syncAction).to.equal('immediate');
    });

    it('should allow constructing a UserAddedEvent with optional fields', () => {
      const event: UserAddedEvent = {
        orgId: 'org123',
        userId: 'user456',
        fullName: 'John Middle Doe',
        firstName: 'John',
        middleName: 'Middle',
        lastName: 'Doe',
        email: 'john@test.com',
        designation: 'Engineer',
        syncAction: SyncAction.None,
      };
      expect(event.firstName).to.equal('John');
      expect(event.middleName).to.equal('Middle');
      expect(event.lastName).to.equal('Doe');
      expect(event.designation).to.equal('Engineer');
    });

    it('should allow constructing a UserDeletedEvent', () => {
      const event: UserDeletedEvent = {
        orgId: 'org123',
        userId: 'user456',
        email: 'john@test.com',
      };
      expect(event.orgId).to.equal('org123');
      expect(event.userId).to.equal('user456');
      expect(event.email).to.equal('john@test.com');
    });

    it('should allow constructing a UserUpdatedEvent', () => {
      const event: UserUpdatedEvent = {
        orgId: 'org123',
        userId: 'user456',
        fullName: 'Jane Doe',
        email: 'jane@test.com',
      };
      expect(event.orgId).to.equal('org123');
      expect(event.fullName).to.equal('Jane Doe');
    });

    it('should allow constructing a full Event object', () => {
      const event: Event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org123',
          userId: 'user456',
          email: 'test@test.com',
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      };
      expect(event.eventType).to.equal('userAdded');
      expect(event.timestamp).to.be.a('number');
      expect(event.payload).to.have.property('orgId');
    });
  });
});

describe('EntitiesEventProducer - additional coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('EntitiesEventProducer class', () => {
    it('should be a class', () => {
      expect(EntitiesEventProducer).to.be.a('function')
    })

    it('should have start method on prototype', () => {
      expect(EntitiesEventProducer.prototype.start).to.be.a('function')
    })

    it('should have stop method on prototype', () => {
      expect(EntitiesEventProducer.prototype.stop).to.be.a('function')
    })

    it('should have publishEvent method on prototype', () => {
      expect(EntitiesEventProducer.prototype.publishEvent).to.be.a('function')
    })
  })

  describe('Event construction patterns', () => {
    it('should construct OrgCreatedEvent', () => {
      const event: Event = {
        eventType: EventType.OrgCreatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          accountType: AccountType.Individual,
          registeredName: 'Test Org',
        } as OrgAddedEvent,
      }
      expect(event.eventType).to.equal('orgCreated')
    })

    it('should construct OrgUpdatedEvent', () => {
      const event: Event = {
        eventType: EventType.OrgUpdatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          registeredName: 'Updated Org',
        } as OrgUpdatedEvent,
      }
      expect(event.eventType).to.equal('orgUpdated')
    })

    it('should construct OrgDeletedEvent', () => {
      const event: Event = {
        eventType: EventType.OrgDeletedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
        } as OrgDeletedEvent,
      }
      expect(event.eventType).to.equal('orgDeleted')
    })

    it('should construct UpdateUserEvent', () => {
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          userId: 'user-1',
          firstName: 'Updated',
          middleName: 'M',
          lastName: 'User',
          fullName: 'Updated M User',
          designation: 'Senior',
          email: 'updated@test.com',
        } as UserUpdatedEvent,
      }
      expect(event.eventType).to.equal('userUpdated')
      expect((event.payload as UserUpdatedEvent).designation).to.equal('Senior')
    })

    it('should construct DeleteUserEvent', () => {
      const event: Event = {
        eventType: EventType.DeleteUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          userId: 'user-1',
          email: 'deleted@test.com',
        } as UserDeletedEvent,
      }
      expect(event.eventType).to.equal('userDeleted')
    })

    it('should construct NewUserEvent with Scheduled sync action', () => {
      const event: Event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          userId: 'user-1',
          email: 'new@test.com',
          syncAction: SyncAction.Scheduled,
        } as UserAddedEvent,
      }
      expect((event.payload as UserAddedEvent).syncAction).to.equal('scheduled')
    })
  })

  describe('publishEvent method', () => {
    // publishEvent no longer sends to the broker. It records the event in the
    // outbox and a dispatcher delivers it, which is what stops a broker
    // failure from being reported to the caller as success.
    afterEach(() => sinon.restore())

    function instanceWith(create: sinon.SinonStub) {
      const instance = Object.create(EntitiesEventProducer.prototype)
      ;(instance as any).topic = 'entity-events'
      ;(instance as any).producer = { isConnected: sinon.stub().returns(true) }
      instance.logger = { info: sinon.stub(), debug: sinon.stub(), error: sinon.stub() }
      sinon.stub(OutboxEvent, 'create').callsFake(create as any)
      return instance
    }

    it('records the event for the entity-events topic instead of sending it', async () => {
      const create = sinon.stub().resolves([{}])
      const instance = instanceWith(create)

      const event: Event = {
        eventType: EventType.OrgCreatedEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          accountType: AccountType.Business,
          registeredName: 'Test Corp',
        } as OrgAddedEvent,
      }

      await instance.publishEvent(event)

      expect(create.calledOnce).to.be.true
      const [docs] = create.firstCall.args
      expect(docs[0].topic).to.equal('entity-events')
      expect(docs[0].key).to.equal(EventType.OrgCreatedEvent)
      expect(JSON.parse(docs[0].value)).to.deep.include({
        eventType: EventType.OrgCreatedEvent,
      })
      expect(docs[0].headers.eventType).to.equal(EventType.OrgCreatedEvent)
      expect(docs[0].status).to.equal('pending')
    })

    it('throws when the event cannot be recorded', async () => {
      // The opposite of the old behaviour, and the point of the change. A
      // failure here means the caller's operation has not fully happened, so
      // the caller hears about it rather than being told all is well.
      const instance = instanceWith(sinon.stub().rejects(new Error('mongo down')))

      const event: Event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: 'org-1',
          userId: 'user-1',
          email: 'test@example.com',
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      }

      try {
        await instance.publishEvent(event)
        expect.fail('expected publishEvent to throw')
      } catch (error) {
        expect((error as Error).message).to.equal('mongo down')
      }
    })

    it('joins the caller transaction when given a session', async () => {
      const create = sinon.stub().resolves([{}])
      const instance = instanceWith(create)
      const session = { id: 'session-1' }

      await instance.publishEvent(
        {
          eventType: EventType.DeleteUserEvent,
          timestamp: 1234567890,
          payload: {
            orgId: 'org-1',
            userId: 'user-1',
            email: 'deleted@test.com',
          } as UserDeletedEvent,
        },
        session,
      )

      expect(create.firstCall.args[1]).to.deep.equal({ session })
    })

    it('records the timestamp header as a string', async () => {
      const create = sinon.stub().resolves([{}])
      const instance = instanceWith(create)

      await instance.publishEvent({
        eventType: EventType.DeleteUserEvent,
        timestamp: 1234567890,
        payload: {
          orgId: 'org-1',
          userId: 'user-1',
          email: 'deleted@test.com',
        } as UserDeletedEvent,
      })

      expect(create.firstCall.args[0][0].headers.timestamp).to.equal('1234567890')
    })
  })

  describe('start and stop methods', () => {
    it('leaves the shared producer connected on stop', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(true),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.stop()
      // The message producer is one instance shared with the notification
      // producer, and the dispatcher publishes from it continuously.
      // Disconnecting it here would break both.
      expect(mockProducer.disconnect.called).to.be.false
    })

    it('should not call disconnect when not connected in stop', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      const mockProducer = {
        isConnected: sinon.stub().returns(false),
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        publish: sinon.stub().resolves(),
        publishBatch: sinon.stub().resolves(),
        healthCheck: sinon.stub().resolves(true),
      }
      ;(instance as any).producer = mockProducer

      await instance.stop()
      expect(mockProducer.disconnect.called).to.be.false
    })
  })
})
