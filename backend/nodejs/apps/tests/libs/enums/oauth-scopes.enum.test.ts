import { expect } from 'chai';
import { OAuthScopeNames } from '../../../src/libs/enums/oauth-scopes.enum';

describe('OAuthScopeNames', () => {
  // Organization Management
  describe('Organization Management scopes', () => {
    it('should have ORG_READ as "org:read"', () => {
      expect(OAuthScopeNames.ORG_READ).to.equal('org:read');
    });

    it('should have ORG_WRITE as "org:write"', () => {
      expect(OAuthScopeNames.ORG_WRITE).to.equal('org:write');
    });

    it('should have ORG_ADMIN as "org:admin"', () => {
      expect(OAuthScopeNames.ORG_ADMIN).to.equal('org:admin');
    });
  });

  // User Management
  describe('User Management scopes', () => {
    it('should have USER_READ as "user:read"', () => {
      expect(OAuthScopeNames.USER_READ).to.equal('user:read');
    });

    it('should have USER_WRITE as "user:write"', () => {
      expect(OAuthScopeNames.USER_WRITE).to.equal('user:write');
    });

    it('should have USER_INVITE as "user:invite"', () => {
      expect(OAuthScopeNames.USER_INVITE).to.equal('user:invite');
    });

    it('should have USER_DELETE as "user:delete"', () => {
      expect(OAuthScopeNames.USER_DELETE).to.equal('user:delete');
    });
  });

  // User Groups
  describe('User Groups scopes', () => {
    it('should have USERGROUP_READ as "usergroup:read"', () => {
      expect(OAuthScopeNames.USERGROUP_READ).to.equal('usergroup:read');
    });

    it('should have USERGROUP_WRITE as "usergroup:write"', () => {
      expect(OAuthScopeNames.USERGROUP_WRITE).to.equal('usergroup:write');
    });
  });

  // Teams
  describe('Teams scopes', () => {
    it('should have TEAM_READ as "team:read"', () => {
      expect(OAuthScopeNames.TEAM_READ).to.equal('team:read');
    });

    it('should have TEAM_WRITE as "team:write"', () => {
      expect(OAuthScopeNames.TEAM_WRITE).to.equal('team:write');
    });
  });

  // Knowledge Base
  describe('Knowledge Base scopes', () => {
    it('should have KB_READ as "kb:read"', () => {
      expect(OAuthScopeNames.KB_READ).to.equal('kb:read');
    });

    it('should have KB_WRITE as "kb:write"', () => {
      expect(OAuthScopeNames.KB_WRITE).to.equal('kb:write');
    });

    it('should have KB_DELETE as "kb:delete"', () => {
      expect(OAuthScopeNames.KB_DELETE).to.equal('kb:delete');
    });

    it('should have KB_UPLOAD as "kb:upload"', () => {
      expect(OAuthScopeNames.KB_UPLOAD).to.equal('kb:upload');
    });
  });

  // Semantic Search
  describe('Semantic Search scopes', () => {
    it('should have SEMANTIC_READ as "semantic:read"', () => {
      expect(OAuthScopeNames.SEMANTIC_READ).to.equal('semantic:read');
    });

    it('should have SEMANTIC_WRITE as "semantic:write"', () => {
      expect(OAuthScopeNames.SEMANTIC_WRITE).to.equal('semantic:write');
    });

    it('should have SEMANTIC_DELETE as "semantic:delete"', () => {
      expect(OAuthScopeNames.SEMANTIC_DELETE).to.equal('semantic:delete');
    });
  });

  // Conversations
  describe('Conversations scopes', () => {
    it('should have CONVERSATION_READ as "conversation:read"', () => {
      expect(OAuthScopeNames.CONVERSATION_READ).to.equal('conversation:read');
    });

    it('should have CONVERSATION_WRITE as "conversation:write"', () => {
      expect(OAuthScopeNames.CONVERSATION_WRITE).to.equal('conversation:write');
    });

    it('should have CONVERSATION_CHAT as "conversation:chat"', () => {
      expect(OAuthScopeNames.CONVERSATION_CHAT).to.equal('conversation:chat');
    });
  });

  // Agents
  describe('Agents scopes', () => {
    it('should have AGENT_READ as "agent:read"', () => {
      expect(OAuthScopeNames.AGENT_READ).to.equal('agent:read');
    });

    it('should have AGENT_WRITE as "agent:write"', () => {
      expect(OAuthScopeNames.AGENT_WRITE).to.equal('agent:write');
    });

    it('should have AGENT_EXECUTE as "agent:execute"', () => {
      expect(OAuthScopeNames.AGENT_EXECUTE).to.equal('agent:execute');
    });
  });

  // Connectors
  describe('Connectors scopes', () => {
    it('should have CONNECTOR_READ as "connector:read"', () => {
      expect(OAuthScopeNames.CONNECTOR_READ).to.equal('connector:read');
    });

    it('should have CONNECTOR_WRITE as "connector:write"', () => {
      expect(OAuthScopeNames.CONNECTOR_WRITE).to.equal('connector:write');
    });

    it('should have CONNECTOR_SYNC as "connector:sync"', () => {
      expect(OAuthScopeNames.CONNECTOR_SYNC).to.equal('connector:sync');
    });

    it('should have CONNECTOR_DELETE as "connector:delete"', () => {
      expect(OAuthScopeNames.CONNECTOR_DELETE).to.equal('connector:delete');
    });
  });

  // Configuration
  describe('Configuration scopes', () => {
    it('should have CONFIG_READ as "config:read"', () => {
      expect(OAuthScopeNames.CONFIG_READ).to.equal('config:read');
    });

    it('should have CONFIG_WRITE as "config:write"', () => {
      expect(OAuthScopeNames.CONFIG_WRITE).to.equal('config:write');
    });
  });

  // Crawling
  describe('Crawling scopes', () => {
    it('should have CRAWL_READ as "crawl:read"', () => {
      expect(OAuthScopeNames.CRAWL_READ).to.equal('crawl:read');
    });

    it('should have CRAWL_WRITE as "crawl:write"', () => {
      expect(OAuthScopeNames.CRAWL_WRITE).to.equal('crawl:write');
    });

    it('should have CRAWL_DELETE as "crawl:delete"', () => {
      expect(OAuthScopeNames.CRAWL_DELETE).to.equal('crawl:delete');
    });
  });

  // OpenID Connect / Identity
  describe('OpenID Connect / Identity scopes', () => {
    it('should have OPENID as "openid"', () => {
      expect(OAuthScopeNames.OPENID).to.equal('openid');
    });

    it('should have PROFILE as "profile"', () => {
      expect(OAuthScopeNames.PROFILE).to.equal('profile');
    });

    it('should have EMAIL as "email"', () => {
      expect(OAuthScopeNames.EMAIL).to.equal('email');
    });

    it('should have OFFLINE_ACCESS as "offline_access"', () => {
      expect(OAuthScopeNames.OFFLINE_ACCESS).to.equal('offline_access');
    });
  });

  // Structural tests
  describe('structural checks', () => {
    it('should have exactly 37 scope entries', () => {
      expect(Object.keys(OAuthScopeNames)).to.have.lengthOf(37);
    });

    it('should contain only the expected keys', () => {
      const expectedKeys = [
        // Organization Management
        'ORG_READ',
        'ORG_WRITE',
        'ORG_ADMIN',
        // User Management
        'USER_READ',
        'USER_WRITE',
        'USER_INVITE',
        'USER_DELETE',
        // User Groups
        'USERGROUP_READ',
        'USERGROUP_WRITE',
        // Teams
        'TEAM_READ',
        'TEAM_WRITE',
        // Knowledge Base
        'KB_READ',
        'KB_WRITE',
        'KB_DELETE',
        'KB_UPLOAD',
        // Semantic Search
        'SEMANTIC_READ',
        'SEMANTIC_WRITE',
        'SEMANTIC_DELETE',
        // Conversations
        'CONVERSATION_READ',
        'CONVERSATION_WRITE',
        'CONVERSATION_CHAT',
        // Agents
        'AGENT_READ',
        'AGENT_WRITE',
        'AGENT_EXECUTE',
        // Connectors
        'CONNECTOR_READ',
        'CONNECTOR_WRITE',
        'CONNECTOR_SYNC',
        'CONNECTOR_DELETE',
        // Configuration
        'CONFIG_READ',
        'CONFIG_WRITE',
        // Crawling
        'CRAWL_READ',
        'CRAWL_WRITE',
        'CRAWL_DELETE',
        // OpenID Connect / Identity
        'OPENID',
        'PROFILE',
        'EMAIL',
        'OFFLINE_ACCESS',
      ];
      expect(Object.keys(OAuthScopeNames)).to.have.members(expectedKeys);
    });

    it('should be frozen (immutable)', () => {
      expect(Object.isFrozen(OAuthScopeNames)).to.be.true;
    });

    it('should have all values as strings', () => {
      Object.values(OAuthScopeNames).forEach((value) => {
        expect(value).to.be.a('string');
      });
    });

    it('should have no duplicate values', () => {
      const values = Object.values(OAuthScopeNames);
      const uniqueValues = new Set(values);
      expect(uniqueValues.size).to.equal(values.length);
    });
  });
});
