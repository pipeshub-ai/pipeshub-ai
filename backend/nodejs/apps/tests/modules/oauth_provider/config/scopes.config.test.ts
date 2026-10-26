import { expect } from 'chai'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  validateScopes,
  getScopesGroupedByCategoryForRole,
  getScopeDefinition,
  getAllScopesGroupedByCategory,
  isValidScope,
  OAuthScopes,
  ScopeCategories,
  DefaultMcpScopes,
  AgentMcpScopes,
  AdminOnlyScopes,
  getAllowedScopeNamesForRole,
} from '../../../../src/modules/oauth_provider/config/scopes.config'
import { OAuthScopeNames } from '../../../../src/libs/enums/oauth-scopes.enum'

describe('oauth_provider/config/scopes.config - coverage', () => {
  it('validateScopes marks unknown scopes invalid', () => {
    const result = validateScopes(['openid', 'not-a-real-scope'])
    expect(result.valid).to.be.false
    expect(result.invalid).to.include('not-a-real-scope')
  })

  // pipeshub_directory lists groups through GET /userGroups, which requires
  // usergroup:read; without it in MCP_SCOPES that call always returns 403.
  it('DefaultMcpScopes covers group listing and grants nothing admin-only', () => {
    expect(DefaultMcpScopes).to.include('usergroup:read')
    expect(DefaultMcpScopes.filter((s) => AdminOnlyScopes.has(s))).to.deep.equal([])
    expect(DefaultMcpScopes.every((s) => isValidScope(s))).to.be.true
  })

  // The code-graph routes and MCP code tools need code:read; the stock presets
  // grant it so new PATs and agent logins reach them without extra setup.
  it('code:read is in the stock presets and open to non-admins', () => {
    expect(DefaultMcpScopes).to.include('code:read')
    expect(AgentMcpScopes).to.include('code:read')
    expect(getAllowedScopeNamesForRole(false)).to.include('code:read')
  })

  // The MCP write tools create collections and upload into them. The agent
  // preset grants that, never delete; and every preset scope must also be in
  // the stock MCP_SCOPES, or Dynamic Client Registration with the default
  // scope set fails with "scope is not in MCP_SCOPES".
  it('the agent preset can create and upload, not delete, and stays inside MCP_SCOPES', () => {
    expect(AgentMcpScopes).to.include.members(['kb:write', 'kb:upload'])
    expect(AgentMcpScopes).to.not.include('kb:delete')
    expect(DefaultMcpScopes).to.include.members([...AgentMcpScopes])
    expect(getAllowedScopeNamesForRole(false)).to.include.members(['kb:write', 'kb:upload'])
  })

  // The first-run script mints the agent token with its own copy of the
  // preset; when the preset grew, that copy was missed twice.
  it('the first-run bootstrap mints exactly the agent preset', () => {
    const script = readFileSync(
      resolve(process.cwd(), '../../../deployment/docker-compose/bootstrap-first-run.sh'),
      'utf8',
    )
    const line = script.split('\n').find((l) => l.startsWith('AGENT_SCOPES=')) ?? ''
    const minted = JSON.parse(line.slice(line.indexOf("'") + 1, line.lastIndexOf("'")))
    expect([...minted].sort()).to.deep.equal([...AgentMcpScopes].sort())
  })

  it('getScopesGroupedByCategoryForRole filters admin-only scopes for non-admin', () => {
    const grouped = getScopesGroupedByCategoryForRole(false)
    const flat = Object.values(grouped).flat()
    expect(flat.some((s) => s.name === 'org:admin')).to.be.false
    expect(flat.some((s) => s.name === 'openid')).to.be.true
  })

  it('getAllScopesGroupedByCategory returns every category bucket', () => {
    const grouped = getAllScopesGroupedByCategory()
    expect(Object.keys(grouped).length).to.be.greaterThan(5)
    expect(grouped.Identity.some((s) => s.name === 'openid')).to.be.true
  })

  it('isValidScope and getScopeDefinition', () => {
    expect(isValidScope('openid')).to.be.true
    expect(getScopeDefinition('openid')?.name).to.equal('openid')
    expect(getScopeDefinition('___missing___')).to.be.undefined
  })

  // requireScopes checks OAuthScopeNames, but an OAuth app can only be granted
  // what OAuthScopes lists, so a scope missing here locks every app out of its routes.
  it('every scope a route can require can be granted to an OAuth app', () => {
    const missing = Object.values(OAuthScopeNames).filter((name) => !(name in OAuthScopes))
    expect(missing).to.deep.equal([])
  })

  // Scopes are grouped by walking ScopeCategories, so one outside it never
  // appears in the OAuth app scope picker.
  it('every grantable scope belongs to a listed category', () => {
    const unlisted = Object.values(OAuthScopes)
      .filter((scope) => !ScopeCategories.includes(scope.category))
      .map((scope) => scope.name)
    expect(unlisted).to.deep.equal([])
  })
})
