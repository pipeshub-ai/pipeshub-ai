import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import { deleteConversationArtifacts } from '../../../../src/modules/artifacts/services/conversation-artifacts.service'
import { InternalServerError, ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { AuthenticatedUserRequest } from '../../../../src/libs/middlewares/types'

const CONVERSATION = '507f1f77bcf86cd799439011'
const ORG = '507f1f77bcf86cd799439012'
const USER = '507f1f77bcf86cd799439013'
const appConfig = {
  connectorBackend: 'http://connector.local',
  storage: { storageType: 'local', endpoint: 'http://storage.local' },
  scopedJwtSecret: 'scoped-secret',
} as AppConfig

const request = () =>
  ({
    headers: { authorization: 'Bearer user-token', cookie: 'session=1' },
    user: { userId: USER, orgId: ORG },
  }) as unknown as AuthenticatedUserRequest

async function caught(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  return undefined
}

describe('deleteConversationArtifacts', () => {
  afterEach(() => sinon.restore())

  it("deletes the caller's artifacts with their own token, then purges the chat's folders for their org", async () => {
    const exec = sinon.stub(connectorUtils, 'executeConnectorCommand')
    exec.onFirstCall().resolves({ statusCode: 200, data: { deleted: 3, kept: 0 } })
    exec.onSecondCall().resolves({ statusCode: 200, data: { purged: 4 } })

    await deleteConversationArtifacts(appConfig, CONVERSATION, request())

    const [uri, method, headers, body, options] = exec.firstCall.args
    expect(uri).to.equal(`http://connector.local/api/v1/artifacts/conversations/${CONVERSATION}`)
    expect(method).to.equal('DELETE')
    expect(headers).to.deep.equal({ authorization: 'Bearer user-token' })
    expect(body).to.equal(undefined)
    expect(options?.timeoutMs).to.be.a('number')

    const [purgeUri, purgeMethod, purgeHeaders, , purgeOptions] = exec.secondCall.args
    expect(purgeUri).to.equal(`http://storage.local/api/v1/document/internal/conversations/${CONVERSATION}/purge`)
    expect(purgeMethod).to.equal('DELETE')
    expect(purgeOptions?.timeoutMs).to.be.a('number')
    const token = String(purgeHeaders.authorization).replace(/^Bearer /, '')
    const claims = jwt.verify(token, 'scoped-secret') as { orgId: string; scopes: string[] }
    expect(claims.orgId).to.equal(ORG)
    expect(claims.scopes).to.deep.equal([TokenScopes.STORAGE_TOKEN])
  })

  it('leaves the folders alone when someone else still owns an artifact of the chat', async () => {
    const exec = sinon.stub(connectorUtils, 'executeConnectorCommand')
    exec.onFirstCall().resolves({ statusCode: 200, data: { deleted: 1, kept: 1 } })

    await deleteConversationArtifacts(appConfig, CONVERSATION, request())

    expect(exec.calledOnce).to.equal(true)
  })

  it('leaves the folders alone when the reply does not say nothing was kept', async () => {
    const exec = sinon.stub(connectorUtils, 'executeConnectorCommand')
    exec.onFirstCall().resolves({ statusCode: 200, data: {} })

    await deleteConversationArtifacts(appConfig, CONVERSATION, request())

    expect(exec.calledOnce).to.equal(true)
  })

  it('fails, so the chat is not deleted, when the artifacts could not be deleted', async () => {
    const exec = sinon.stub(connectorUtils, 'executeConnectorCommand')
    exec.onFirstCall().resolves({ statusCode: 503, data: { detail: 'retry' } })

    expect(await caught(deleteConversationArtifacts(appConfig, CONVERSATION, request()))).to.be.instanceOf(ServiceUnavailableError)
    expect(exec.calledOnce).to.equal(true)
  })

  it('fails, so the chat is not deleted, when storage could not purge the folders', async () => {
    const exec = sinon.stub(connectorUtils, 'executeConnectorCommand')
    exec.onFirstCall().resolves({ statusCode: 200, data: { deleted: 0, kept: 0 } })
    exec.onSecondCall().resolves({ statusCode: 503, data: { message: 'disk' } })

    expect(await caught(deleteConversationArtifacts(appConfig, CONVERSATION, request()))).to.be.instanceOf(ServiceUnavailableError)
  })

  it('fails when the connectors service errors or cannot be reached', async () => {
    const exec = sinon.stub(connectorUtils, 'executeConnectorCommand')
    exec.onFirstCall().resolves({ statusCode: 500, data: { detail: 'boom' } })
    exec.onSecondCall().rejects(new TypeError('fetch failed'))

    expect(await caught(deleteConversationArtifacts(appConfig, CONVERSATION, request()))).to.be.instanceOf(InternalServerError)
    expect(await caught(deleteConversationArtifacts(appConfig, CONVERSATION, request()))).to.be.instanceOf(ServiceUnavailableError)
  })
})
