import { expect } from 'chai'
import {
  validateScopes,
  getScopesGroupedByCategoryForRole,
  getScopeDefinition,
  getAllScopesGroupedByCategory,
  isValidScope,
} from '../../../../src/modules/oauth_provider/config/scopes.config'

describe('oauth_provider/config/scopes.config - coverage', () => {
  it('validateScopes marks unknown scopes invalid', () => {
    const result = validateScopes(['openid', 'not-a-real-scope'])
    expect(result.valid).to.be.false
    expect(result.invalid).to.include('not-a-real-scope')
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
})
