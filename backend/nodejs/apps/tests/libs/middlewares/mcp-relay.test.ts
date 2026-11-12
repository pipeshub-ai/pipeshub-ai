import { expect } from 'chai'
import express from 'express'
import { AddressInfo } from 'net'
import jwt from 'jsonwebtoken'
import {
  isMcpRelay,
  MCP_RELAY_HEADER,
  relayKey,
  signMcpRelay,
} from '../../../src/libs/middlewares/mcp-relay'
import { createGlobalRateLimiter } from '../../../src/libs/middlewares/rate-limit.middleware'
import { createMockLogger } from '../../helpers/mock-logger'

const SECRET = 'scoped-secret-for-tests'
const key = relayKey(SECRET)
const reqWith = (bearer: string | undefined, relay: string | undefined): any => ({
  headers: {
    ...(bearer === undefined ? {} : { authorization: `Bearer ${bearer}` }),
    ...(relay === undefined ? {} : { [MCP_RELAY_HEADER]: relay }),
  },
})

describe('mcp relay header', () => {
  it('holds for the token it was signed for', () => {
    expect(isMcpRelay(reqWith('tok', signMcpRelay(key, 'tok')), key)).to.be.true
  })

  it('does not carry over to another token, another key, or a request without a token', () => {
    const relay = signMcpRelay(key, 'tok')
    expect(isMcpRelay(reqWith('other', relay), key)).to.be.false
    expect(isMcpRelay(reqWith('tok', relay), relayKey('another-secret'))).to.be.false
    expect(isMcpRelay(reqWith(undefined, relay), key)).to.be.false
    expect(isMcpRelay(reqWith('tok', undefined), key)).to.be.false
  })

  it('refuses a token signed with the key for another purpose', () => {
    const other = jwt.sign({ scopes: ['caller:role'], th: 'x' }, SECRET, { algorithm: 'HS256' })
    expect(isMcpRelay(reqWith('tok', other), key)).to.be.false
  })

  describe('global rate limiter', () => {
    async function twoCalls(headers: Record<string, string>, path = '/api/v1/search'): Promise<number[]> {
      const app = express()
      app.use(createGlobalRateLimiter(createMockLogger() as any, 1, SECRET))
      app.get(/.*/, (_req, res) => { res.status(204).end() })
      const server = app.listen(0)
      try {
        const { port } = server.address() as AddressInfo
        const call = () => fetch(`http://127.0.0.1:${port}${path}`, { headers }).then((r) => r.status)
        return [await call(), await call()]
      } finally {
        server.close()
      }
    }

    it('counts ordinary calls from one address', async () => {
      expect(await twoCalls({ authorization: 'Bearer tok' })).to.deep.equal([204, 429])
    })

    it("does not count an /mcp tool call's own API calls again", async () => {
      const headers = { authorization: 'Bearer tok', [MCP_RELAY_HEADER]: signMcpRelay(key, 'tok') }
      expect(await twoCalls(headers)).to.deep.equal([204, 204])
    })

    it('leaves /mcp to its own per-account limits', async () => {
      expect(await twoCalls({}, '/mcp')).to.deep.equal([204, 204])
    })
  })
})
