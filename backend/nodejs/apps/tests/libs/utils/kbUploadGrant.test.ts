import { expect } from 'chai'
import {
  deriveKbUploadGrantKey,
  signKbUploadGrant,
  KB_UPLOAD_GRANT_TTL_SECONDS,
} from '../../../src/libs/utils/kbUploadGrant'

const decode = (token: string) =>
  JSON.parse(Buffer.from(token.split('.')[0] ?? '', 'base64url').toString())

describe('kbUploadGrant', () => {
  const base = {
    secret: 'vector-secret',
    orgId: 'o',
    userId: 'u',
    kbId: 'k',
    folderId: 'f',
    documents: [
      { recordKey: 'kb', documentId: 'b', mimeType: 'text/b', extension: 'pdf' },
      { recordKey: 'ka', documentId: 'a', mimeType: 'text/a', extension: '' },
    ],
    now: 1000,
  }

  // Shared with test_kb_upload_grant.py::test_the_node_signer_and_this_one_agree,
  // which the connector's verifier is tested against.
  it('signs the same bytes as the connector service', () => {
    expect(signKbUploadGrant(base)).to.equal(
      'eyJkb2N1bWVudHMiOltbImthIiwiYSIsInRleHQvYSIsIiJdLFsia2IiLCJiIiwidGV4dC9iIiwicGRmIl1dLCJleHAiOjEzMDAsImZvbGRlcklkIjoiZiIsImlhdCI6MTAwMCwia2JJZCI6ImsiLCJvcmdJZCI6Im8iLCJ1c2VySWQiOiJ1IiwidiI6MX0.beAZ_yJDODilzVKHMv0rKTlqhpAOUBxA3JyP7WIEOIw',
    )
  })

  it('binds org, user, KB, folder and the exact document ids, with a short lifetime', () => {
    const claims = decode(signKbUploadGrant(base))
    expect(claims).to.deep.equal({
      v: 1, orgId: 'o', userId: 'u', kbId: 'k', folderId: 'f',
      documents: [['ka', 'a', 'text/a', ''], ['kb', 'b', 'text/b', 'pdf']], iat: 1000, exp: 1000 + KB_UPLOAD_GRANT_TTL_SECONDS,
    })
  })

  it('binds the KB root as an empty folder', () => {
    expect(decode(signKbUploadGrant({ ...base, folderId: undefined })).folderId).to.equal('')
  })

  it('refuses a repeated record key or storage document', () => {
    expect(() => signKbUploadGrant({ ...base, documents: [{ recordKey: 'k', documentId: 'a', mimeType: 'm', extension: '' }, { recordKey: 'k', documentId: 'b', mimeType: 'm', extension: '' }] })).to.throw()
    expect(() => signKbUploadGrant({ ...base, documents: [{ recordKey: 'k1', documentId: 'a', mimeType: 'm', extension: '' }, { recordKey: 'k2', documentId: 'a', mimeType: 'm', extension: '' }] })).to.throw()
  })

  it('refuses to sign without a secret, an identity or any document', () => {
    expect(() => signKbUploadGrant({ ...base, secret: '' })).to.throw(/not configured/)
    expect(() => signKbUploadGrant({ ...base, userId: '' })).to.throw()
    expect(() => signKbUploadGrant({ ...base, orgId: '' })).to.throw()
    expect(() => signKbUploadGrant({ ...base, kbId: '' })).to.throw()
    expect(() => signKbUploadGrant({ ...base, documents: [] })).to.throw()
    expect(() => signKbUploadGrant({ ...base, documents: [{ recordKey: 'k', documentId: 'a', mimeType: '', extension: '' }] })).to.throw()
  })

  it('uses a key that is not the raw secret', () => {
    expect(deriveKbUploadGrantKey('s').toString()).to.not.equal('s')
    expect(() => deriveKbUploadGrantKey('')).to.throw()
  })
})
