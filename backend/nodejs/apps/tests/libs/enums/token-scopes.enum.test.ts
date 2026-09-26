import { expect } from 'chai';
import {
  TokenScopes,
  USER_ACTION_TOKEN_SCOPES,
  isUserActionScope,
} from '../../../src/libs/enums/token-scopes.enum';

describe('TokenScopes', () => {
  it('should have SEND_MAIL as "mail:send"', () => {
    expect(TokenScopes.SEND_MAIL).to.equal('mail:send');
  });

  it('should have FETCH_CONFIG as "fetch:config"', () => {
    expect(TokenScopes.FETCH_CONFIG).to.equal('fetch:config');
  });

  it('should have PASSWORD_RESET as "password:reset"', () => {
    expect(TokenScopes.PASSWORD_RESET).to.equal('password:reset');
  });

  it('should have USER_LOOKUP as "user:lookup"', () => {
    expect(TokenScopes.USER_LOOKUP).to.equal('user:lookup');
  });

  it('should have TOKEN_REFRESH as "token:refresh"', () => {
    expect(TokenScopes.TOKEN_REFRESH).to.equal('token:refresh');
  });

  it('should have STORAGE_TOKEN as "storage:token"', () => {
    expect(TokenScopes.STORAGE_TOKEN).to.equal('storage:token');
  });

  it('should have CONVERSATION_CREATE as "conversation:create"', () => {
    expect(TokenScopes.CONVERSATION_CREATE).to.equal('conversation:create');
  });

  it('should have CONVERSATION_PERMISSIONS as "conversation:permissions"', () => {
    expect(TokenScopes.CONVERSATION_PERMISSIONS).to.equal('conversation:permissions');
  });
  it('should have VALIDATE_EMAIL as "email:validate"', () => {
    expect(TokenScopes.VALIDATE_EMAIL).to.equal('email:validate');
  });

  it('should have ORG_EMAIL_VERIFY as "org:email:verify"', () => {
    expect(TokenScopes.ORG_EMAIL_VERIFY).to.equal('org:email:verify');
  });

  it('should have EMAIL_VERIFIED as "email:verified"', () => {
    expect(TokenScopes.EMAIL_VERIFIED).to.equal('email:verified');
  });

  it('should have exactly 12 scopes', () => {
    expect(Object.keys(TokenScopes)).to.have.lengthOf(12);
  });

  it('should contain only the expected keys', () => {
    const expectedKeys = [
      'SEND_MAIL',
      'FETCH_CONFIG',
      'PASSWORD_RESET',
      'USER_LOOKUP',
      'TOKEN_REFRESH',
      'STORAGE_TOKEN',
      'CONVERSATION_CREATE',
      'CONVERSATION_PERMISSIONS',
      'VALIDATE_EMAIL',
      'ORG_EMAIL_VERIFY',
      'EMAIL_VERIFIED',
      'DESKTOP_COMMAND',
    ];
    expect(Object.keys(TokenScopes)).to.have.members(expectedKeys);
  });

  it('should be frozen (immutable)', () => {
    expect(Object.isFrozen(TokenScopes)).to.be.true;
  });

  it('should have all values as strings', () => {
    Object.values(TokenScopes).forEach((value) => {
      expect(value).to.be.a('string');
    });
  });

  it('should have no duplicate values', () => {
    const values = Object.values(TokenScopes);
    const uniqueValues = new Set(values);
    expect(uniqueValues.size).to.equal(values.length);
  });
});

describe('isUserActionScope', () => {
  it('should be true for scopes on user-held tokens', () => {
    [
      TokenScopes.PASSWORD_RESET,
      TokenScopes.VALIDATE_EMAIL,
      TokenScopes.TOKEN_REFRESH,
      TokenScopes.ORG_EMAIL_VERIFY,
      TokenScopes.EMAIL_VERIFIED,
    ].forEach((scope) => expect(isUserActionScope(scope)).to.be.true);
    expect(USER_ACTION_TOKEN_SCOPES.size).to.equal(5);
  });

  it('should be false for service scopes and unknown strings', () => {
    [
      TokenScopes.SEND_MAIL,
      TokenScopes.FETCH_CONFIG,
      TokenScopes.USER_LOOKUP,
      TokenScopes.STORAGE_TOKEN,
      TokenScopes.CONVERSATION_CREATE,
      'not:a:scope',
    ].forEach((scope) => expect(isUserActionScope(scope)).to.be.false);
  });
});
