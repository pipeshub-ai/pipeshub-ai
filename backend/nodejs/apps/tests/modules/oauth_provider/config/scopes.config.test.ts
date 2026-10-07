import { expect } from 'chai'
import {
  validateScopes,
  getScopesGroupedByCategoryForRole,
  getScopeDefinition,
  getAllScopesGroupedByCategory,
  isValidScope,
  OAuthScopes,
  ScopeCategories,
  DefaultMcpScopes,
  AdminOnlyScopes,
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
