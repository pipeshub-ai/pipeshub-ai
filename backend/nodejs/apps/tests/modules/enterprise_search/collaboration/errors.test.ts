import { expect } from 'chai';
import { DomainHttpError } from '../../../../src/libs/errors/domain-http.error';
import * as errors from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors';

const cases: Array<[DomainHttpError, number, string]> = [
  [new errors.ConversationNotFoundError(), 404, 'CONVERSATION_NOT_FOUND'],
  [new errors.ConversationReadOnlyError(), 403, 'CONVERSATION_READ_ONLY'],
  [new errors.ConversationOwnerOnlyError(), 403, 'CONVERSATION_OWNER_ONLY'],
  [new errors.RegenerateNotAllowedError(), 403, 'REGENERATE_NOT_ALLOWED'],
  [new errors.ResumeNotAllowedError(), 403, 'RESUME_NOT_ALLOWED'],
  [new errors.RunLostError(), 409, 'RUN_LOST'],
  [new errors.OwnerInactiveError(), 403, 'OWNER_INACTIVE'],
  [new errors.ProjectAccessRequiredError('p1'), 403, 'PROJECT_ACCESS_REQUIRED'],
  [new errors.TeamResolutionUnavailableError(), 503, 'TEAM_RESOLUTION_UNAVAILABLE'],
  [new errors.OwnerStatusUnavailableError(), 503, 'OWNER_STATUS_UNAVAILABLE'],
  [new errors.ConversationBusyError({ userId: 'u1', startedAt: new Date(0) }), 409, 'CONVERSATION_BUSY'],
  [new errors.ConversationChangedError(2), 409, 'CONVERSATION_CHANGED'],
  [new errors.DuplicateMessageError('m1', true), 409, 'DUPLICATE_MESSAGE'],
  [new errors.CollaboratorLimitError(50), 409, 'COLLABORATOR_LIMIT'],
  [new errors.ConnectorSetupRequiredError(['gmail']), 412, 'CONNECTOR_SETUP_REQUIRED'],
  [new errors.InvalidPrincipalError([{ key: 'user:u1', reason: 'x' }]), 400, 'INVALID_PRINCIPAL'],
  [new errors.RateLimitedError(30), 429, 'RATE_LIMITED'],
  [new errors.OrgWideConfirmationRequiredError(), 400, 'ORG_WIDE_CONFIRMATION_REQUIRED'],
];

describe('collaboration domain errors', () => {
  for (const [error, status, code] of cases) {
    it(`${error.constructor.name} is ${status} ${code}`, () => {
      expect(error).to.be.instanceOf(DomainHttpError);
      expect(error.statusCode).to.equal(status);
      expect(error.code).to.equal(code);
    });
  }

  it('covers every code in the catalog', () => {
    expect(cases.map(([, , code]) => code).sort()).to.deep.equal(Object.values(errors.COLLAB_ERROR_CODES).sort());
  });

  it('ConversationNotFoundError carries no details', () => {
    expect(new errors.ConversationNotFoundError().publicDetails).to.equal(undefined);
  });

  it('ConversationBusyError details contain ISO strings only', () => {
    const e = new errors.ConversationBusyError({ userId: 'u1', displayName: 'Ann', startedAt: new Date('2026-01-02T03:04:05.000Z') });
    expect(e.publicDetails).to.deep.equal({
      activeRun: { userId: 'u1', displayName: 'Ann', startedAt: '2026-01-02T03:04:05.000Z' },
    });
  });

  it('InvalidPrincipalError exposes keys only, not the reasons', () => {
    const e = new errors.InvalidPrincipalError([{ key: 'team:t1', reason: 'other org' }]);
    expect(e.publicDetails).to.deep.equal({ principalIds: ['team:t1'] });
  });
});
