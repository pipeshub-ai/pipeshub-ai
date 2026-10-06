import 'reflect-metadata'
import { expect } from 'chai'
import jwt from 'jsonwebtoken'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  filterOwnedAttachments,
  mintConversationPermissionsToken,
} from '../../../../src/modules/enterprise_search/utils/attachment-validation'

const appConfig = {
  aiBackend: 'http://ai.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const decode = (token: string): Record<string, unknown> =>
  jwt.verify(token, 'test-scoped-secret') as Record<string, unknown>

const keys = (claims: Record<string, unknown>): string[] => Object.keys(claims).sort()

describe('PH02-06 conversation:permissions service token claims', () => {
  afterEach(() => sinon.restore())

  it('mints exactly {userId, orgId, scopes} + iat/exp with a 300 s lifetime, ids stringified', () => {
    const claims = decode(mintConversationPermissionsToken(appConfig, { userId: { toString: () => 'u1' }, orgId: 7 }))
    expect(keys(claims)).to.deep.equal(['exp', 'iat', 'orgId', 'scopes', 'userId'])
    expect(claims.userId).to.equal('u1')
    expect(claims.orgId).to.equal('7')
    expect(claims.scopes).to.deep.equal(['conversation:permissions'])
    expect((claims.exp as number) - (claims.iat as number)).to.equal(300)
  })

  it('adds isServiceAccount only when the caller is a service account', () => {
    const flagged = decode(mintConversationPermissionsToken(appConfig, { userId: 'u', orgId: 'o', isServiceAccount: true }))
    expect(keys(flagged)).to.deep.equal(['exp', 'iat', 'isServiceAccount', 'orgId', 'scopes', 'userId'])
    expect(flagged.isServiceAccount).to.equal(true)
    const plain = decode(mintConversationPermissionsToken(appConfig, { userId: 'u', orgId: 'o', isServiceAccount: false }))
    expect(keys(plain)).to.deep.equal(['exp', 'iat', 'orgId', 'scopes', 'userId'])
  })

  it('the attachment-validate call sends the same exact claims, with and without isServiceAccount', async () => {
    const auth: string[] = []
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: { headers: Record<string, string> }) {
      auth.push(this.headers.authorization ?? this.headers.Authorization ?? '')
      return { statusCode: 200, data: { recordIds: ['r1'] } } as never
    })
    await filterOwnedAttachments(appConfig, { userId: 'u1', orgId: 'o1' }, [{ recordId: 'r1', recordName: 'a' } as never])
    await filterOwnedAttachments(appConfig, { userId: 'u1', orgId: 'o1', isServiceAccount: true }, [{ recordId: 'r1', recordName: 'a' } as never])
    expect(auth).to.have.length(2)
    const [plain, flagged] = auth.map((h) => decode(h.replace(/^Bearer /, '')))
    expect(keys(plain!)).to.deep.equal(['exp', 'iat', 'orgId', 'scopes', 'userId'])
    expect(keys(flagged!)).to.deep.equal(['exp', 'iat', 'isServiceAccount', 'orgId', 'scopes', 'userId'])
    expect((plain!.exp as number) - (plain!.iat as number)).to.equal(300)
  })
})
