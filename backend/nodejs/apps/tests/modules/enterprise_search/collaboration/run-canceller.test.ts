import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'
import { IServiceTokenIssuer, ServiceTokenClaims } from '../../../../src/libs/services/service-token.issuer'
import { setConversationContext } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { RunCanceller } from '../../../../src/modules/enterprise_search/services/collaboration/http/run-canceller'
import { GrantedRole } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types'

class RecordingIssuer implements IServiceTokenIssuer {
  readonly issued: Array<{ claims: ServiceTokenClaims; ttl: string }> = []
  issue(claims: ServiceTokenClaims, ttl: '1m' | '5m'): string {
    this.issued.push({ claims, ttl })
    return 'minted-token'
  }
}

const CONV = 'conv-1'
const RUN = '11111111-1111-4111-8111-111111111111'

function requestAs(userId: string, role: GrantedRole): any {
  const caller = { userId, orgId: 'org-1', teamIds: { ids: [], complete: true } }
  const req: any = { headers: { authorization: 'Bearer user-jwt', host: 'x' } }
  setConversationContext(req, { caller, collab: true, grant: { role, caller, session: { _id: CONV }, via: [], view: {} } } as never)
  return req
}

describe('RunCanceller', () => {
  let fetchStub: sinon.SinonStub
  let issuer: RecordingIssuer
  let canceller: RunCanceller

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch').resolves(
      new Response(JSON.stringify({ cancelled: true }), { status: 200, headers: { 'content-type': 'application/json' } }),
    )
    issuer = new RecordingIssuer()
    canceller = new RunCanceller(issuer, () => 'http://ai.test')
  })
  afterEach(() => sinon.restore())

  const call = () => ({
    url: String(fetchStub.firstCall.args[0]),
    headers: Object.fromEntries(new Headers(fetchStub.firstCall.args[1].headers).entries()),
    body: JSON.parse(fetchStub.firstCall.args[1].body),
  })

  it('the starter cancels with their own token on /chat/cancel and no service token is minted', async () => {
    await canceller.cancel(requestAs('user-a', 'owner'), CONV, { runId: RUN, starterUserId: 'user-a' })

    expect(call().url).to.equal('http://ai.test/api/v1/chat/cancel')
    expect(call().headers.authorization).to.equal('Bearer user-jwt')
    expect(call().body).to.deep.equal({ runId: RUN, conversationId: CONV })
    expect(issuer.issued).to.have.length(0)
  })

  it('an unknown starter (flag off) keeps the legacy call', async () => {
    await canceller.cancel(requestAs('user-a', 'owner'), CONV, { runId: RUN })

    expect(call().url).to.equal('http://ai.test/api/v1/chat/cancel')
    expect(issuer.issued).to.have.length(0)
  })

  it('an editor cancelling another user’s run mints a 1m token bound to the conversation and run and calls the participant URL', async () => {
    await canceller.cancel(requestAs('user-b', 'write'), CONV, { runId: RUN, starterUserId: 'user-a' })

    expect(issuer.issued).to.deep.equal([
      {
        claims: { userId: 'user-b', orgId: 'org-1', scopes: [TokenScopes.CONVERSATION_CANCEL], conversationId: CONV, runId: RUN },
        ttl: '1m',
      },
    ])
    expect(call().url).to.equal('http://ai.test/api/v1/chat/cancel/participant')
    expect(call().headers.authorization).to.equal('Bearer minted-token')
    expect(call().body).to.deep.equal({ runId: RUN, conversationId: CONV })
  })

  it('the owner cancelling an editor’s run uses the participant path too', async () => {
    await canceller.cancel(requestAs('user-a', 'owner'), CONV, { runId: RUN, starterUserId: 'user-b' })

    expect(call().url).to.equal('http://ai.test/api/v1/chat/cancel/participant')
  })

  it('a reader cancelling another user’s run is refused before any token is minted or call made', async () => {
    let thrown: any
    try {
      await canceller.cancel(requestAs('user-c', 'read'), CONV, { runId: RUN, starterUserId: 'user-a' })
    } catch (error) {
      thrown = error
    }

    expect(thrown?.statusCode).to.equal(403)
    expect(issuer.issued).to.have.length(0)
    expect(fetchStub.called).to.equal(false)
  })
})
