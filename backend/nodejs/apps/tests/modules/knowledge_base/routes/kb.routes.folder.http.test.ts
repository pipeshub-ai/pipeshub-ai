import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  FOLDER_ID,
  KB_ID,
  KbHarness,
  MEMBER,
  call,
  sessionToken,
  startKbHarness,
} from './kb-http-harness'

/**
 * Route-level integration tests for the folder-creation endpoints.
 *
 * The key correctness property being tested:
 *   POST /:kbId/folder/:folderId/subfolder
 *     must forward to  POST /api/v1/kb/:kbId/folder/:folderId/subfolder   (nested)
 *     and must NOT forward to POST /api/v1/kb/:kbId/folder                 (root)
 *
 * Before the fix, the handler only read req.query.folderId.  Calling the
 * subfolder route without a ?folderId= query param silently fell back to the
 * root-folder connector endpoint, creating the folder at the KB root instead
 * of under the requested parent.
 */
describe('Knowledge base routes over HTTP: folder creation', () => {
  let h: KbHarness
  let token: string

  beforeEach(async () => {
    h = await startKbHarness()
    token = sessionToken(h, MEMBER)
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  describe('POST /:kbId/folder — root folder creation', () => {
    it('forwards to the connector root-folder endpoint', async () => {
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder`, {
        status: 201,
        body: { id: FOLDER_ID },
      })

      const r = await call(h, 'POST', `/${KB_ID}/folder`, {
        token,
        json: { folderName: 'Top Level' },
      })

      expect(r.status).to.equal(201)
      const calls = h.backend.calls.map((c) => `${c.method} ${c.path}`)
      expect(calls).to.deep.equal([`POST /api/v1/kb/${KB_ID}/folder`])
    })

    it('does not hit the subfolder endpoint for root-folder creation', async () => {
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder`, {
        status: 201,
        body: { id: FOLDER_ID },
      })

      await call(h, 'POST', `/${KB_ID}/folder`, {
        token,
        json: { folderName: 'Top Level' },
      })

      const subfolderCalls = h.backend.calls.filter((c) =>
        c.path.includes('/subfolder'),
      )
      expect(subfolderCalls).to.have.length(0)
    })
  })

  describe('POST /:kbId/folder/:folderId/subfolder — nested folder creation', () => {
    it('forwards to the connector subfolder endpoint using the path-param folderId', async () => {
      h.backend.on(
        'POST',
        `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`,
        { status: 201, body: { id: 'child-folder-id' } },
      )

      const r = await call(
        h,
        'POST',
        `/${KB_ID}/folder/${FOLDER_ID}/subfolder`,
        { token, json: { folderName: 'Child Folder' } },
      )

      expect(r.status).to.equal(201)
      const calls = h.backend.calls.map((c) => `${c.method} ${c.path}`)
      expect(calls).to.deep.equal([
        `POST /api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`,
      ])
    })

    it('does NOT fall back to the root-folder connector endpoint when no query param is given', async () => {
      // Only register the subfolder endpoint.  If the handler mistakenly hits
      // the root-folder endpoint the test catches it via the call list.
      h.backend.on(
        'POST',
        `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`,
        { status: 201, body: { id: 'child-folder-id' } },
      )

      // No ?folderId= query parameter — the route must use the path param.
      const r = await call(
        h,
        'POST',
        `/${KB_ID}/folder/${FOLDER_ID}/subfolder`,
        { token, json: { folderName: 'Child Folder' } },
      )

      expect(r.status, 'handler should succeed with the path-param folderId').to.equal(201)

      const rootFolderCalls = h.backend.calls.filter(
        (c) => c.path === `/api/v1/kb/${KB_ID}/folder` && c.method === 'POST',
      )
      expect(
        rootFolderCalls,
        'must not have silently fallen back to the root-folder endpoint',
      ).to.have.length(0)
    })

    it('sends the correct folder name in the request body', async () => {
      h.backend.on(
        'POST',
        `/api/v1/kb/${KB_ID}/folder/${FOLDER_ID}/subfolder`,
        { status: 201, body: { id: 'child-folder-id' } },
      )

      await call(h, 'POST', `/${KB_ID}/folder/${FOLDER_ID}/subfolder`, {
        token,
        json: { folderName: 'Design Docs' },
      })

      const recorded = h.backend.calls[0]
      expect(recorded).to.exist
      expect((recorded!.body as Record<string, unknown>).name).to.equal('Design Docs')
    })
  })
})
