import { expect } from 'chai'
import { mcpLinkRouting } from '../../../src/modules/mcp/public-links'

describe('mcpLinkRouting', () => {
  const loopback = 'http://127.0.0.1:3000/api/v1'

  it('keeps the loopback base when the public origin is the same', () => {
    const links = mcpLinkRouting(loopback, 'http://127.0.0.1:3000')
    expect(links.serverURL).to.equal(loopback)
  })

  it('builds links on the public origin and sends the request to loopback', () => {
    const links = mcpLinkRouting(loopback, 'https://pipeshub.example.com/some/path')
    expect(links.serverURL).to.equal('https://pipeshub.example.com/api/v1')
    const sent = links.toLoopback(
      new Request('https://pipeshub.example.com/api/v1/connectors?x=1', {
        headers: { authorization: 'Bearer t' },
      }),
    )
    expect(sent.url).to.equal('http://127.0.0.1:3000/api/v1/connectors?x=1')
    expect(sent.headers.get('authorization')).to.equal('Bearer t')
  })

  it('refuses a request outside the API base rather than sending the token there', () => {
    const links = mcpLinkRouting(loopback, 'https://pipeshub.example.com')
    expect(() =>
      links.toLoopback(new Request('https://pipeshub.example.com/elsewhere')),
    ).to.throw('outside the API base URL')
  })
})
