import { expect } from 'chai';
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum';

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

  it('should have exactly 7 scopes', () => {
    expect(Object.keys(TokenScopes)).to.have.lengthOf(7);
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
