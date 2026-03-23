import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  EntitiesEventProducer,
  EventType,
  SyncAction,
  AccountType,
  Event,
  OrgAddedEvent,
  OrgDeletedEvent,
  OrgUpdatedEvent,
  UserAddedEvent,
  UserDeletedEvent,
  UserUpdatedEvent,
} from '../../../../src/modules/user_management/services/entity_events.service'

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
    it('should publish event to entity-events topic', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      ;(instance as any).topic = 'entity-events'
      instance.publish = sinon.stub().resolves()
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

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

      expect(instance.publish.calledOnce).to.be.true
      const [topic, message] = instance.publish.firstCall.args
      expect(topic).to.equal('entity-events')
      expect(message.key).to.equal(EventType.OrgCreatedEvent)
      expect(JSON.parse(message.value)).to.deep.include({ eventType: EventType.OrgCreatedEvent })
      expect(message.headers.eventType).to.equal(EventType.OrgCreatedEvent)
      expect(instance.logger.info.calledOnce).to.be.true
    })

    it('should log error when publish fails', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      ;(instance as any).topic = 'entity-events'
      instance.publish = sinon.stub().rejects(new Error('Publish error'))
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

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

      await instance.publishEvent(event)
      expect(instance.logger.error.calledOnce).to.be.true
    })

    it('should include timestamp header as string', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      ;(instance as any).topic = 'entity-events'
      instance.publish = sinon.stub().resolves()
      instance.logger = { info: sinon.stub(), error: sinon.stub() }

      const timestamp = 1234567890
      const event: Event = {
        eventType: EventType.DeleteUserEvent,
        timestamp,
        payload: {
          orgId: 'org-1',
          userId: 'user-1',
          email: 'deleted@test.com',
        } as UserDeletedEvent,
      }

      await instance.publishEvent(event)

      const message = instance.publish.firstCall.args[1]
      expect(message.headers.timestamp).to.equal('1234567890')
    })
  })

  describe('start and stop methods', () => {
    it('should call disconnect when connected in stop', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      instance.isConnected = sinon.stub().returns(true)
      instance.disconnect = sinon.stub().resolves()

      await instance.stop()
      expect(instance.disconnect.calledOnce).to.be.true
    })

    it('should not call disconnect when not connected in stop', async () => {
      const instance = Object.create(EntitiesEventProducer.prototype)
      instance.isConnected = sinon.stub().returns(false)
      instance.disconnect = sinon.stub().resolves()

      await instance.stop()
      expect(instance.disconnect.called).to.be.false
    })
  })
})
