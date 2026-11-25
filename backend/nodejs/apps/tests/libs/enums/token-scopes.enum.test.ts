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

  it('should have ENTITY_USER_WRITE as "entity:user:write"', () => {
    expect(TokenScopes.ENTITY_USER_WRITE).to.equal('entity:user:write');
  });

  it('should have ORG_EMAIL_VERIFY as "org:email:verify"', () => {
    expect(TokenScopes.ORG_EMAIL_VERIFY).to.equal('org:email:verify');
  });

  it('should have EMAIL_VERIFIED as "email:verified"', () => {
    expect(TokenScopes.EMAIL_VERIFIED).to.equal('email:verified');
  });

  it('should have CONVERSATION_FORK as "conversation:fork"', () => {
    expect(TokenScopes.CONVERSATION_FORK).to.equal('conversation:fork');
  });

  it('should have AGENT_ERASE as "agent:erase"', () => {
    expect(TokenScopes.AGENT_ERASE).to.equal('agent:erase');
  });

  it('should have AGENT_NAMES as "agent:names"', () => {
    expect(TokenScopes.AGENT_NAMES).to.equal('agent:names');
  });

  it('should have DESKTOP_COMMAND as "desktop:command"', () => {
    expect(TokenScopes.DESKTOP_COMMAND).to.equal('desktop:command');
  });

  it('should have CALLER_ROLE as "caller:role"', () => {
    expect(TokenScopes.CALLER_ROLE).to.equal('caller:role');
  });

  it('should have MCP_RELAY as "mcp:relay"', () => {
    expect(TokenScopes.MCP_RELAY).to.equal('mcp:relay');
  });

  it('should have SLACK_BOT_VERIFY as "slack-bot:verify"', () => {
    expect(TokenScopes.SLACK_BOT_VERIFY).to.equal('slack-bot:verify');
  });

  it('should have CONTROL_PLANE_BINDINGS as "control-plane:bindings"', () => {
    expect(TokenScopes.CONTROL_PLANE_BINDINGS).to.equal('control-plane:bindings');
  });

  it('should have exactly 25 scopes', () => {
    expect(Object.keys(TokenScopes)).to.have.lengthOf(25);
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
      'CONVERSATION_ERASE',
      'CONVERSATION_FORK',
      'AGENT_ERASE',
      'AGENT_NAMES',
      'VALIDATE_EMAIL',
      'ENTITY_USER_WRITE',
      'ORG_EMAIL_VERIFY',
      'EMAIL_VERIFIED',
      'DESKTOP_COMMAND',
      'CALLER_ROLE',
      'MCP_RELAY',
      'SLACK_BOT_VERIFY',
      'CONTROL_PLANE_BINDINGS',
      'CAPABILITIES_READ',
      'DIRECTORY_READ',
      'CAPABILITIES_SYNC',
      'PRINCIPAL_ERASE',
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
      TokenScopes.DESKTOP_COMMAND,
      TokenScopes.CALLER_ROLE,
      TokenScopes.MCP_RELAY,
      TokenScopes.SLACK_BOT_VERIFY,
      TokenScopes.CONTROL_PLANE_BINDINGS,
      TokenScopes.ENTITY_USER_WRITE,
      TokenScopes.PRINCIPAL_ERASE,
      'not:a:scope',
    ].forEach((scope) => expect(isUserActionScope(scope)).to.be.false);
  });
});
