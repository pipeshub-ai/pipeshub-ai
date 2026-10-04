import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  FOLDER_ID,
  KB_ID,
  KB_ROUTES,
  KbHarness,
  KbRoute,
  MEMBER,
  RECORD_GROUP_ID,
  RECORD_ID,
  call,
  errorMessage,
  rawCall,
  sessionToken,
  startKbHarness,
} from './kb-http-harness'
import { INVALID_PATH_SEGMENT_MESSAGE } from '../../../../src/libs/middlewares/safe-path-params.middleware'

const VALID: Record<string, string> = {
  kbId: KB_ID,
  folderId: FOLDER_ID,
  recordId: RECORD_ID,
  recordGroupId: RECORD_GROUP_ID,
  parentType: 'kb',
  parentId: KB_ID,
}

// Express decodes these inside a path parameter; pasted into the connector
// service URL they become separators, a query, a fragment or a second escape.
const ENCODED = ['..%2F..%2Fadmin', 'kb%2Fother', 'abc%3Fx%3D1', 'a%23b', 'a%5Cb', 'a%2525b', 'a%00b', 'a%0Ab']
// Sent as written: fetch() would resolve these away before Express saw them.
const DOT_SEGMENTS = ['..', '%2E%2E', '.', '%2E', '%20..%20']

const paramsOf = (pattern: string): string[] => [...pattern.matchAll(/:(\w+)/g)].map((m) => m[1] as string)

const pathWith = (route: KbRoute, param: string, value: string): string =>
  route.pattern.replace(/:(\w+)/g, (_m, name: string) => (name === param ? value : (VALID[name] as string)))

describe('Knowledge base routes over HTTP: path parameters stay one segment', () => {
  let h: KbHarness

  beforeEach(async () => {
    h = await startKbHarness()
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  const withParams = KB_ROUTES.filter((r) => paramsOf(r.pattern).length > 0)

  it('knows a real value for every parameter the router declares', () => {
    const declared = new Set(withParams.flatMap((r) => paramsOf(r.pattern)))
    expect([...declared].sort()).to.deep.equal(Object.keys(VALID).sort())
    expect(withParams).to.have.length(19)
  })

  for (const route of withParams) {
    for (const param of paramsOf(route.pattern)) {
      it(`${route.method} ${route.pattern} refuses a ${param} that is not one plain segment`, async () => {
        const token = sessionToken(h, MEMBER)
        for (const value of ENCODED) {
          const r = await call(h, route.method, pathWith(route, param, value), { token, json: route.json, form: route.form?.() })
          expect(r.status, `${param}=${value}`).to.equal(400)
          expect(errorMessage(r), `${param}=${value}`).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
        }
        for (const value of DOT_SEGMENTS) {
          const r = await rawCall(h, route.method, pathWith(route, param, value), token, route.json)
          expect(r.status, `${param}=${value}`).to.equal(400)
          expect(errorMessage(r), `${param}=${value}`).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
        }
        expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([])
      })
    }
  }

  it('still forwards an id with spaces or dots inside it to its own path', async () => {
    h.backend.on('GET', '/api/v1/knowledge-hub/nodes/SHAREPOINT%20ONLINE/site.v2', { status: 200, body: { items: [] } })
    const r = await call(h, 'GET', '/knowledge-hub/nodes/SHAREPOINT%20ONLINE/site.v2', { token: sessionToken(h, MEMBER) })
    expect(r.status).to.equal(200)
    expect(h.backend.calls.map((c) => c.path)).to.deep.equal(['/api/v1/knowledge-hub/nodes/SHAREPOINT%20ONLINE/site.v2'])
  })
})
