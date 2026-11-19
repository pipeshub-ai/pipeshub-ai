import { expect } from 'chai'
import { insufficientScopeChallenge, missingToolScopes } from '../../../src/modules/mcp/step-up'

const required = (tool: string) =>
  ({ pipeshub_upload: ['kb:upload'], pipeshub_search: ['semantic:write'] } as Record<string, string[]>)[tool]

const call = (name: string) => ({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: {} } })

describe('MCP step-up', () => {
  it('names the scopes a tool call needs that the token lacks', () => {
    expect(missingToolScopes(call('pipeshub_upload'), ['kb:read'], required)).to.deep.equal(['kb:upload'])
  })

  it('finds nothing missing when the token covers the call, for other methods, or for tools it does not know', () => {
    expect(missingToolScopes(call('pipeshub_search'), ['semantic:write'], required)).to.deep.equal([])
    expect(missingToolScopes({ method: 'tools/list' }, [], required)).to.deep.equal([])
    expect(missingToolScopes(call('code_search'), [], required)).to.deep.equal([])
    expect(missingToolScopes(undefined, [], required)).to.deep.equal([])
  })

  it('looks at every call in a batch', () => {
    expect(missingToolScopes([call('pipeshub_search'), call('pipeshub_upload')], [], required))
      .to.deep.equal(['semantic:write', 'kb:upload'])
  })

  it('asks for what the token has plus what is missing', () => {
    expect(insufficientScopeChallenge(['kb:read'], ['kb:upload'], 'https://x/.well-known/oauth-protected-resource/mcp'))
      .to.equal('Bearer error="insufficient_scope", scope="kb:read kb:upload", resource_metadata="https://x/.well-known/oauth-protected-resource/mcp"')
  })
})
