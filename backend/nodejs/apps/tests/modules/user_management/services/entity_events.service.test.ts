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
