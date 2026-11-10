import 'reflect-metadata'
import { expect } from 'chai'
import {
  fetchClientMetadataDocument,
  isBlockedAddress,
  isClientIdMetadataUrl,
  parseClientIdUrl,
  setClientMetadataGetter,
} from '../../../../src/modules/oauth_provider/services/client_metadata_document'
import { InvalidClientError } from '../../../../src/libs/errors/oauth.errors'

const ID = 'https://client.example.com/oauth/metadata.json'
const serve = (doc: unknown) =>
  setClientMetadataGetter(async () => (typeof doc === 'string' ? doc : JSON.stringify(doc)))

async function rejection(p: Promise<unknown>): Promise<Error> {
  try {
    await p
  } catch (e) {
    return e as Error
  }
  throw new Error('expected a rejection')
}

describe('client_metadata_document', () => {
  afterEach(() => setClientMetadataGetter())

  it('treats only https client ids as metadata documents', () => {
    expect(isClientIdMetadataUrl(ID)).to.be.true
    expect(isClientIdMetadataUrl('3f9c0d2e-uuid')).to.be.false
    expect(isClientIdMetadataUrl('http://client.example.com/m.json')).to.be.false
    expect(isClientIdMetadataUrl(undefined)).to.be.false
  })

  it('rejects client id URLs the draft forbids', () => {
    for (const bad of [
      'https://client.example.com/',
      'https://client.example.com',
      'https://user:pw@client.example.com/m.json',
      'https://client.example.com/m.json#frag',
      'https://client.example.com/a/../m.json',
      'https://CLIENT.example.com/m.json',
      `https://client.example.com/${'a'.repeat(200)}`,
    ]) {
      expect(() => parseClientIdUrl(bad), bad).to.throw(InvalidClientError)
    }
    expect(parseClientIdUrl(ID).host).to.equal('client.example.com')
  })

  it('blocks loopback, private, link-local and mapped addresses but not public ones', () => {
    for (const a of ['127.0.0.1', '10.1.2.3', '172.16.0.1', '192.168.1.1', '169.254.169.254', '100.64.0.1', '0.0.0.0', '::1', 'fd00::1', 'fe80::1', '::ffff:127.0.0.1', 'not-an-ip']) {
      expect(isBlockedAddress(a), a).to.be.true
    }
    for (const a of ['8.8.8.8', '1.1.1.1', '2606:4700:4700::1111']) {
      expect(isBlockedAddress(a), a).to.be.false
    }
  })

  it('refuses to fetch a client id that is a private address', async () => {
    const err = await rejection(fetchClientMetadataDocument('https://127.0.0.1/m.json'))
    expect(err).to.be.instanceOf(InvalidClientError)
    expect(err.message).to.include('non-public')
  })

  it('returns a valid document', async () => {
    serve({
      client_id: ID,
      client_name: 'Example',
      redirect_uris: ['http://localhost/callback'],
      grant_types: ['authorization_code', 'refresh_token'],
      token_endpoint_auth_method: 'none',
      logo_uri: 'https://client.example.com/logo.png',
    })
    const doc = await fetchClientMetadataDocument(ID)
    expect(doc.client_name).to.equal('Example')
    expect(doc.redirect_uris).to.deep.equal(['http://localhost/callback'])
    expect(doc.logo_uri).to.equal('https://client.example.com/logo.png')
  })

  it('rejects documents that do not identify the client or ask for a secret', async () => {
    const cases: Array<[unknown, string]> = [
      ['not json', 'not JSON'],
      [[1], 'not a JSON object'],
      [{ client_id: 'https://evil.example.com/m.json', redirect_uris: ['https://a/cb'] }, 'different client_id'],
      [{ client_id: ID, redirect_uris: ['https://a/cb'], client_secret: 's' }, 'client secret'],
      [{ client_id: ID, redirect_uris: ['https://a/cb'], token_endpoint_auth_method: 'client_secret_basic' }, 'not supported'],
      [{ client_id: ID, redirect_uris: [] }, 'redirect_uris'],
      [{ client_id: ID }, 'redirect_uris'],
      [{ client_id: ID, redirect_uris: ['https://a/cb'], grant_types: 'authorization_code' }, 'grant_types'],
    ]
    for (const [doc, msg] of cases) {
      serve(doc)
      const err = await rejection(fetchClientMetadataDocument(ID))
      expect(err, msg).to.be.instanceOf(InvalidClientError)
      expect(err.message, msg).to.include(msg)
    }
  })

  it('turns a failed fetch into an invalid client', async () => {
    setClientMetadataGetter(async () => {
      throw new Error('HTTP 404')
    })
    const err = await rejection(fetchClientMetadataDocument(ID))
    expect(err).to.be.instanceOf(InvalidClientError)
    expect(err.message).to.include('HTTP 404')
  })
})
