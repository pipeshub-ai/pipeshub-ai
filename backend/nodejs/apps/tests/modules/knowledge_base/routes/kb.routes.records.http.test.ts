import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  FOLDER_ID,
  INTERNAL_DETAIL,
  KB_ID,
  KB_ROUTES,
  KbHarness,
  MEMBER,
  ORG_B,
  OTHER_KB_ID,
  OUTSIDER,
  RECORD_ID,
  call,
  callRoute,
  errorMessage,
  sessionToken,
  startKbHarness,
} from './kb-http-harness'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../../src/libs/errors/backend-error'

const TRACEBACK = {
  detail:
    'Traceback (most recent call last):\n  File "/app/kb_service.py", line 88, in move\narango.exceptions.AQLQueryExecuteError: [HTTP 500] at 127.0.0.1:8529',
}

describe('Knowledge base routes over HTTP: browsing and changing records', () => {
  let h: KbHarness
  let token: string

  const forwarded = (method: string, path: string) => h.backend.callsTo(method, path)

  beforeEach(async () => {
    h = await startKbHarness()
    token = sessionToken(h, MEMBER)
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  describe('pagination', () => {
    it('asks for the first page of twenty, sorted by name, when nothing is said', async () => {
      h.backend.on('GET', '/api/v1/kb/', { status: 200, body: { knowledgeBases: [] } })

      await call(h, 'GET', '/', { token })

      expect(Object.fromEntries(forwarded('GET', '/api/v1/kb/')[0]!.query)).to.deep.equal({
        page: '1',
        limit: '20',
        sort_by: 'name',
        sort_order: 'asc',
      })
    })

    it('passes on the page, size, order, search and role filter asked for', async () => {
      h.backend.on('GET', '/api/v1/kb/', { status: 200, body: { knowledgeBases: [], pagination: { page: 3 } } })

      const r = await call(h, 'GET', '/?page=3&limit=100&sortBy=updatedAtTimestamp&sortOrder=desc&search=%20road%20map%20&permissions=OWNER,WRITER', { token })

      expect(r.status).to.equal(200)
      expect(r.body.pagination).to.deep.equal({ page: 3 })
      expect(Object.fromEntries(forwarded('GET', '/api/v1/kb/')[0]!.query)).to.deep.equal({
        page: '3',
        limit: '100',
        search: 'road map',
        permissions: 'OWNER,WRITER',
        sort_by: 'updatedAtTimestamp',
        sort_order: 'desc',
      })
    })

    for (const q of [
      'page=0',
      'page=-1',
      'page=abc',
      'page=99999999999999999999',
      'limit=0',
      'limit=101',
      'sortBy=password',
      'sortOrder=sideways',
      'permissions=ADMIN',
      'search=%3Cscript%3Ealert(1)%3C%2Fscript%3E',
      'search=%25s%25n',
    ]) {
      it(`refuses ?${q.slice(0, 40)} before asking the connector service`, async () => {
        const r = await call(h, 'GET', `/?${q}`, { token })
        expect(r.status).to.equal(400)
        expect(h.backend.calls).to.deep.equal([])
      })
    }

    it('maps knowledge hub paging and filters to the connector service, keeping explicit false flags', async () => {
      h.backend.on('GET', `/api/v1/knowledge-hub/nodes/folder/${FOLDER_ID}`, { status: 200, body: { items: [], total: 0 } })

      await call(
        h,
        'GET',
        `/knowledge-hub/nodes/folder/${FOLDER_ID}?page=2&limit=50&sortBy=name&sortOrder=desc&q=plan&nodeTypes=record&onlyContainers=false&flattened=false&orgId=${ORG_B}&userId=${OUTSIDER._id}`,
        { token },
      )

      expect(Object.fromEntries(forwarded('GET', `/api/v1/knowledge-hub/nodes/folder/${FOLDER_ID}`)[0]!.query)).to.deep.equal({
        page: '2',
        limit: '50',
        sort_by: 'name',
        sort_order: 'desc',
        q: 'plan',
        node_types: 'record',
        only_containers: 'false',
        flattened: 'false',
      })
    })
  })

  describe('reading', () => {
    it('drops internal folder links from a knowledge base and from the list', async () => {
      const folders = [{ id: FOLDER_ID, name: 'Specs', webUrl: '/kb/internal/folder' }]
      h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID, folders } })
      h.backend.on('GET', '/api/v1/kb/', { status: 200, body: { knowledgeBases: [{ id: KB_ID, folders }, null] } })

      const one = await call(h, 'GET', `/${KB_ID}`, { token })
      const list = await call(h, 'GET', '/', { token })

      expect(one.body.folders).to.deep.equal([{ id: FOLDER_ID, name: 'Specs' }])
      expect((list.body.knowledgeBases as Array<{ folders: unknown }>)[0]!.folders).to.deep.equal([{ id: FOLDER_ID, name: 'Specs' }])
    })

    it('drops graph database keys from a record', async () => {
      h.backend.on('GET', `/api/v1/records/${RECORD_ID}`, {
        status: 200,
        body: { record: { id: RECORD_ID, _id: 'records/1', _rev: '_a', fileRecord: { name: 'a.pdf', _id: 'files/1', _key: '1', _rev: '_b' } } },
      })

      const r = await call(h, 'GET', `/record/${RECORD_ID}`, { token })

      expect(r.body.record).to.deep.equal({ id: RECORD_ID, fileRecord: { name: 'a.pdf' } })
    })

    it('says a missing record was not found', async () => {
      h.backend.on('GET', `/api/v1/records/${RECORD_ID}`, { status: 404, body: { detail: 'Record not found' } })

      const r = await call(h, 'GET', `/record/${RECORD_ID}`, { token })

      expect(r.status).to.equal(404)
      expect(errorMessage(r)).to.equal(`Record ${RECORD_ID} not found`)
    })

    it('lists who has access to a knowledge base', async () => {
      h.backend.on('GET', `/api/v1/kb/${KB_ID}/permissions`, {
        status: 200,
        body: { permissions: [{ userId: MEMBER._id, role: 'OWNER' }], totalCount: 1, internalCursor: 'x' },
      })

      const r = await call(h, 'GET', `/${KB_ID}/permissions`, { token })

      expect(r.body).to.deep.equal({ kbId: KB_ID, permissions: [{ userId: MEMBER._id, role: 'OWNER' }], totalCount: 1 })
    })

    it('streams a file with its type, name, version and conversion', async () => {
      h.backend.on('GET', `/api/v1/stream/record/${RECORD_ID}`, {
        status: 200,
        headers: { 'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="a.pdf"' },
        raw: '%PDF-1.4 body',
      })

      const r = await call(h, 'GET', `/stream/record/${RECORD_ID}?convertTo=pdf&version=2`, { token })

      expect(r.status).to.equal(200)
      expect(r.text).to.equal('%PDF-1.4 body')
      expect(r.headers.get('content-type')).to.equal('application/pdf')
      expect(r.headers.get('content-disposition')).to.equal('attachment; filename="a.pdf"')
      expect(Object.fromEntries(forwarded('GET', `/api/v1/stream/record/${RECORD_ID}`)[0]!.query)).to.deep.equal({ convertTo: 'pdf', version: '2' })
    })

    it('refuses a file version that is not a whole number', async () => {
      const r = await call(h, 'GET', `/stream/record/${RECORD_ID}?version=2%26admin%3D1`, { token })
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })

    it("passes on the source's wait time when a file is rate limited", async function () {
      // The shared axios instance retries a 429 after its Retry-After, three times.
      this.timeout(15000)
      h.backend.on('GET', `/api/v1/stream/record/${RECORD_ID}`, {
        status: 429,
        headers: { 'retry-after': '1' },
        body: { detail: 'Google Drive is rate limiting downloads. Try again shortly.' },
      })

      const r = await call(h, 'GET', `/stream/record/${RECORD_ID}`, { token })

      expect(r.status).to.equal(429)
      expect(r.headers.get('retry-after')).to.equal('1')
      expect(r.body).to.deep.equal({ error: 'Google Drive is rate limiting downloads. Try again shortly.' })
    })
  })

  describe('moving', () => {
    const move = (json: unknown) => call(h, 'PUT', `/${KB_ID}/record/${RECORD_ID}/move`, { token, json })
    const MOVE = `/api/v1/kb/${KB_ID}/record/${RECORD_ID}/move`

    it('moves within the knowledge base in the path, whatever the body says', async () => {
      h.backend.on('PUT', MOVE, { status: 200, body: { success: true } })

      const r = await move({ newParentId: FOLDER_ID, kbId: OTHER_KB_ID })

      expect(r.status).to.equal(200)
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([`PUT ${MOVE}`])
      expect(forwarded('PUT', MOVE)[0]!.body).to.deep.equal({ newParentId: FOLDER_ID })
    })

    it('moves to the top of the knowledge base when the new parent is null', async () => {
      h.backend.on('PUT', MOVE, { status: 200, body: { success: true } })

      await move({ newParentId: null })

      expect(forwarded('PUT', MOVE)[0]!.body).to.deep.equal({ newParentId: null })
    })

    it('refuses a move that does not say where to', async () => {
      const r = await move({})
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })

    for (const [what, status, detail] of [
      ['into its own subfolder', 400, 'A folder cannot be moved into one of its own subfolders'],
      ['into a folder of another knowledge base', 404, 'Destination folder not found in this knowledge base'],
      ['next to an item with the same name', 409, 'An item named "Specs" already exists in the destination folder'],
    ] as const) {
      it(`passes on the refusal to move ${what}`, async () => {
        h.backend.on('PUT', MOVE, { status, body: { detail } })

        const r = await move({ newParentId: FOLDER_ID })

        expect(r.status).to.equal(status)
        expect(errorMessage(r)).to.equal(detail)
      })
    }

    it('refuses a knowledge base id that is not a UUID', async () => {
      const r = await call(h, 'PUT', `/kb-1/record/${RECORD_ID}/move`, { token, json: { newParentId: null } })
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })
  })

  describe('renaming', () => {
    const renames = [
      { what: 'a knowledge base', path: `/${KB_ID}`, field: 'kbName', forwards: `/api/v1/kb/${KB_ID}` },
      { what: 'a folder', path: `/${KB_ID}/folder/${FOLDER_ID}`, field: 'folderName', forwards: `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}` },
    ]

    for (const rename of renames) {
      it(`renames ${rename.what} to exactly the name given`, async () => {
        h.backend.on('PUT', rename.forwards, { status: 200, body: { success: true } })

        const r = await call(h, 'PUT', rename.path, { token, json: { [rename.field]: 'Road map 2027' } })

        expect(r.status).to.equal(200)
        expect(forwarded('PUT', rename.forwards)[0]!.body).to.deep.equal({ name: 'Road map 2027' })
      })

      it(`passes on the refusal to rename ${rename.what} to a name already taken`, async () => {
        h.backend.on('PUT', rename.forwards, { status: 409, body: { detail: 'That name is already in use here' } })

        const r = await call(h, 'PUT', rename.path, { token, json: { [rename.field]: 'Taken' } })

        expect(r.status).to.equal(409)
        expect(errorMessage(r)).to.equal('That name is already in use here')
      })

      for (const bad of ['<img src=x onerror=alert(1)>', 'report %s %n', 'x'.repeat(256)]) {
        it(`refuses to rename ${rename.what} to "${bad.slice(0, 20)}"`, async () => {
          const r = await call(h, 'PUT', rename.path, { token, json: { [rename.field]: bad } })
          expect(r.status).to.equal(400)
          expect(h.backend.calls).to.deep.equal([])
        })
      }
    }

    it('refuses to rename a folder to nothing', async () => {
      const r = await call(h, 'PUT', `/${KB_ID}/folder/${FOLDER_ID}`, { token, json: { folderName: '' } })
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })

    it('renames a record without touching its file', async () => {
      h.backend.on('PUT', `/api/v1/kb/record/${RECORD_ID}`, { status: 200, body: { updatedRecord: { id: RECORD_ID, recordName: 'Plan' } } })

      const r = await call(h, 'PUT', `/record/${RECORD_ID}`, { token, json: { recordName: 'Plan' } })

      expect(r.status).to.equal(200)
      expect(r.body).to.include({ message: 'Record updated successfully', fileUploaded: false })
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([`PUT /api/v1/kb/record/${RECORD_ID}`])
      expect(forwarded('PUT', `/api/v1/kb/record/${RECORD_ID}`)[0]!.body).to.deep.equal({ updates: { recordName: 'Plan' }, fileMetadata: null })
    })

    it('passes on the refusal to give a record a name already taken', async () => {
      h.backend.on('PUT', `/api/v1/kb/record/${RECORD_ID}`, { status: 409, body: { detail: 'A record named "Plan" already exists here' } })

      const r = await call(h, 'PUT', `/record/${RECORD_ID}`, { token, json: { recordName: 'Plan' } })

      expect(r.status).to.equal(409)
      expect(errorMessage(r)).to.equal('A record named "Plan" already exists here')
    })

    it('refuses a record name with markup in it', async () => {
      const r = await call(h, 'PUT', `/record/${RECORD_ID}`, { token, json: { recordName: '<script>x</script>' } })
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })

    it('creates a nested folder under the folder asked for', async () => {
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`, { status: 201, body: { id: 'f2', webUrl: '/internal' } })

      const r = await call(h, 'POST', `/${KB_ID}/folder?folderId=${FOLDER_ID}`, { token, json: { folderName: 'Drafts' } })

      expect(r.status).to.equal(201)
      expect(r.body).to.deep.equal({ id: 'f2' })
      expect(forwarded('POST', `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`)[0]!.body).to.deep.equal({ name: 'Drafts' })
    })
  })

  describe('deleting', () => {
    const deletes = [
      { what: 'a knowledge base', path: `/${KB_ID}`, forwards: `/api/v1/kb/${KB_ID}` },
      { what: 'a folder', path: `/${KB_ID}/folder/${FOLDER_ID}`, forwards: `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}` },
      { what: 'a record', path: `/record/${RECORD_ID}`, forwards: `/api/v1/records/${RECORD_ID}` },
    ]

    for (const del of deletes) {
      it(`leaves the records and stored files of ${del.what} to the connector service, in one call`, async () => {
        h.backend.on('DELETE', del.forwards, { status: 200, body: { success: true, deletedRecords: 3 } })

        const r = await call(h, 'DELETE', del.path, { token })

        expect(r.status).to.equal(200)
        expect(r.body).to.deep.equal({ success: true, deletedRecords: 3 })
        expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([`DELETE ${del.forwards}`])
      })

      it(`says ${del.what} that is already gone was not found`, async () => {
        h.backend.on('DELETE', del.forwards, { status: 404, body: { detail: 'Not found' } })

        const r = await call(h, 'DELETE', del.path, { token })

        expect(r.status).to.equal(404)
        expect(errorMessage(r)).to.equal('Not found')
      })
    }

    it('refuses to delete a knowledge base for a member who may only read it', async () => {
      h.backend.on('DELETE', `/api/v1/kb/${KB_ID}`, { status: 403, body: { detail: 'Only an owner can delete this knowledge base' } })

      const r = await call(h, 'DELETE', `/${KB_ID}`, { token })

      expect(r.status).to.equal(403)
      expect(errorMessage(r)).to.equal('Only an owner can delete this knowledge base')
    })
  })

  describe('error responses', () => {
    const jsonRoutes = KB_ROUTES.filter((r) => r.forwards && !r.form && !r.pattern.startsWith('/stream'))

    it('covers every route that answers in JSON', () => {
      expect(jsonRoutes).to.have.length(23)
    })

    it("never shows a connector service traceback, address or stack", async () => {
      for (const route of jsonRoutes) {
        h.backend.reset()
        const [method, path] = route.forwards!.split(' ') as [string, string]
        h.backend.on(method, path, { status: 500, body: TRACEBACK })

        const r = await callRoute(h, route, route.caller ? sessionToken(h, route.caller) : token)

        expect(r.status, `${route.method} ${route.pattern}`).to.equal(500)
        expect(errorMessage(r), route.pattern).to.match(/^Something went wrong/)
        for (const pattern of INTERNAL_DETAIL) expect(r.text, `${route.pattern} ${pattern}`).to.not.match(pattern)
      }
    })

    const dropOn = async (pattern: string, method: string) => {
      const route = jsonRoutes.find((r) => r.pattern === pattern && r.method === method)!
      const [fwdMethod, fwdPath] = route.forwards!.split(' ') as [string, string]
      h.backend.reset()
      h.backend.on(fwdMethod, fwdPath, 'drop')
      return callRoute(h, route, token)
    }

    it('says plainly that a service is unreachable when the connection drops', async function () {
      // Each call is retried with backoff before giving up.
      this.timeout(30000)
      for (const [method, pattern] of [
        ['POST', '/'],
        ['GET', '/knowledge-hub/nodes'],
        ['PUT', '/record/:recordId'],
        ['GET', '/:kbId'],
        ['POST', '/:kbId/permissions'],
        ['PUT', '/:kbId/record/:recordId/move'],
      ] as const) {
        const r = await dropOn(pattern, method)
        expect(r.status, `${method} ${pattern}`).to.equal(503)
        expect(errorMessage(r), `${method} ${pattern}`).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
      }
    })

    it('keeps internals out of a record delete that loses its connection', async function () {
      this.timeout(10000)
      const r = await dropOn('/record/:recordId', 'DELETE')
      expect(r.status).to.be.within(500, 503)
      for (const pattern of INTERNAL_DETAIL) expect(r.text).to.not.match(pattern)
    })

    it('keeps the wait time when the connector service is busy', async () => {
      h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 503, headers: { 'retry-after': '7' }, body: { detail: 'arangodb pool exhausted' } })

      const r = await call(h, 'GET', `/${KB_ID}`, { token })

      expect(r.status).to.equal(503)
      expect(r.headers.get('retry-after')).to.equal('7')
      expect(errorMessage(r)).to.equal('This part of PipesHub is briefly unavailable. Please try again in 7 seconds.')
    })
  })

  describe('reindexing', () => {
    it('asks for a reindex of just this record, unforced, unless told otherwise', async () => {
      h.backend.on('POST', `/api/v1/records/${RECORD_ID}/reindex`, { status: 200, body: { success: true } })

      await call(h, 'POST', `/reindex/record/${RECORD_ID}`, { token, json: {} })
      await call(h, 'POST', `/reindex/record/${RECORD_ID}`, { token, json: { depth: 2, statusFilters: ['FAILED'] } })

      expect(forwarded('POST', `/api/v1/records/${RECORD_ID}/reindex`).map((c) => c.body)).to.deep.equal([
        { depth: 0, force: false },
        { depth: 2, force: false, statusFilters: ['FAILED'] },
      ])
    })

    it('refuses a reindex deeper than a hundred levels', async () => {
      const r = await call(h, 'POST', `/reindex/record-group/rg-1`, { token, json: { depth: 101 } })
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })
  })
})
