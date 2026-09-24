/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'

import {
  listSkills,
  getSkill,
  createSkill,
  updateSkill,
  deleteSkill,
  searchSkills,
  getSkillCategories,
  patchSkillBody,
  deprecateSkill,
  getSkillUsage,
  listSkillVersions,
  getSkillVersion,
  rollbackSkill,
  getSkillResource,
  writeSkillResource,
  removeSkillResource,
  getPendingSkillCandidates,
  approveSkillCandidate,
  rejectSkillCandidate,
  previewNpmSkillImport,
  previewUrlSkillImport,
  finalizeSkillImport,
  previewUploadSkillImport,
  exportSkill,
} from '../../../../src/modules/skills/controller/skills.controller'

function makeReq(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer tok', 'content-type': 'application/json' },
    body: {},
    query: {},
    params: {},
    ...overrides,
  }
}

function makeRes(): any {
  const res: any = {}
  res.status = sinon.stub().returns(res)
  res.json = sinon.stub().returns(res)
  res.send = sinon.stub().returns(res)
  res.setHeader = sinon.stub().returns(res)
  return res
}

const appConfig: any = { aiBackend: 'http://ai:8000' }

describe('SkillsController', () => {
  afterEach(() => sinon.restore())

  // ===================== forwardJson-generated handlers =====================

  describe('listSkills (GET)', () => {
    it('should forward to /api/v1/skills/ and return upstream JSON', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: [{ name: 's1' }] })
      const res = makeRes()
      const req = makeReq({ query: { page: '1' } })

      await listSkills(appConfig)(req, res, sinon.stub())

      expect(stub.calledOnce).to.be.true
      const cfg = stub.firstCall.args[0]
      expect(cfg.url).to.equal('http://ai:8000/api/v1/skills/')
      expect(cfg.method).to.equal('GET')
      expect(cfg.params).to.deep.equal({ page: '1' })
      expect(cfg.data).to.be.undefined
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith([{ name: 's1' }])).to.be.true
    })

    it('should forward non-200 upstream status', async () => {
      sinon.stub(axios, 'request').resolves({ status: 503, data: { detail: 'down' } })
      const res = makeRes()

      await listSkills(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(503)).to.be.true
      expect(res.json.calledWith({ detail: 'down' })).to.be.true
    })

    it('should call next on upstream response error', async () => {
      sinon.stub(axios, 'request').rejects({
        response: { status: 500, data: { detail: 'db error' } },
      })
      const next = sinon.stub()

      await listSkills(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('db error')
    })

    it('should call next with ServiceUnavailableError on network error', async () => {
      sinon.stub(axios, 'request').rejects({ code: 'ECONNREFUSED' })
      const next = sinon.stub()

      await listSkills(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('unavailable')
    })
  })

  describe('getSkill (GET with params)', () => {
    it('should URL-encode skill name in path', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: { name: 'my skill' } })
      const req = makeReq({ params: { name: 'my skill' } })

      await getSkill(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.equal('http://ai:8000/api/v1/skills/my%20skill')
    })

    it('should handle special characters in name', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'test/slash&amp' } })

      await getSkill(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('test%2Fslash%26amp')
    })
  })

  describe('createSkill (POST)', () => {
    it('should forward body as data', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 201, data: { name: 'new-skill' } })
      const body = { name: 'new-skill', body: '# My skill' }
      const res = makeRes()

      await createSkill(appConfig)(makeReq({ body }), res, sinon.stub())

      expect(stub.firstCall.args[0].data).to.deep.equal(body)
      expect(stub.firstCall.args[0].method).to.equal('POST')
      expect(res.status.calledWith(201)).to.be.true
    })
  })

  describe('updateSkill (PUT)', () => {
    it('should PUT to named skill path with body', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'sk1' }, body: { body: 'updated' } })

      await updateSkill(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.equal('http://ai:8000/api/v1/skills/sk1')
      expect(stub.firstCall.args[0].method).to.equal('PUT')
      expect(stub.firstCall.args[0].data).to.deep.equal({ body: 'updated' })
    })
  })

  describe('deleteSkill (DELETE)', () => {
    it('should DELETE without body and forward query', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: { deleted: true } })
      const req = makeReq({ params: { name: 'old-skill' }, query: { detach: 'true' } })
      const res = makeRes()

      await deleteSkill(appConfig)(req, res, sinon.stub())

      expect(stub.firstCall.args[0].method).to.equal('DELETE')
      expect(stub.firstCall.args[0].data).to.be.undefined
      expect(stub.firstCall.args[0].params).to.deep.equal({ detach: 'true' })
      expect(res.status.calledWith(200)).to.be.true
    })

    it('should forward 409 safe-delete payload verbatim', async () => {
      sinon.stub(axios, 'request').resolves({
        status: 409,
        data: { detail: 'in use', usedByAgents: ['a1'], requiredBySkills: ['s1'] },
      })
      const res = makeRes()

      await deleteSkill(appConfig)(makeReq({ params: { name: 'busy' } }), res, sinon.stub())

      expect(res.status.calledWith(409)).to.be.true
      expect(res.json.firstCall.args[0].usedByAgents).to.deep.equal(['a1'])
    })
  })

  describe('patchSkillBody (PATCH)', () => {
    it('should PATCH the /body sub-path', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'sk1' }, body: { body: 'patched' } })

      await patchSkillBody(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/sk1/body')
      expect(stub.firstCall.args[0].method).to.equal('PATCH')
    })
  })

  describe('searchSkills (GET)', () => {
    it('should forward search query params', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: [] })
      const req = makeReq({ query: { q: 'hello', category: 'email' } })

      await searchSkills(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.equal('http://ai:8000/api/v1/skills/search')
      expect(stub.firstCall.args[0].params).to.deep.equal({ q: 'hello', category: 'email' })
    })
  })

  describe('getSkillCategories', () => {
    it('should GET /categories', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: ['email', 'slack'] })
      const res = makeRes()

      await getSkillCategories(appConfig)(makeReq(), res, sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/categories')
      expect(res.json.calledWith(['email', 'slack'])).to.be.true
    })
  })

  describe('version history handlers', () => {
    it('listSkillVersions should GET /:name/versions', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: [{ v: 1 }] })
      const req = makeReq({ params: { name: 'sk1' } })

      await listSkillVersions(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/sk1/versions')
    })

    it('getSkillVersion should GET /:name/versions/:version', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'sk1', version: '3' } })

      await getSkillVersion(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/sk1/versions/3')
    })

    it('rollbackSkill should POST /:name/rollback', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'sk1' }, body: { version: 2 } })

      await rollbackSkill(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/sk1/rollback')
      expect(stub.firstCall.args[0].method).to.equal('POST')
    })
  })

  describe('resource handlers', () => {
    it('getSkillResource should GET /:name/resource', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: { content: 'abc' } })
      const req = makeReq({ params: { name: 'sk1' } })

      await getSkillResource(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/sk1/resource')
      expect(stub.firstCall.args[0].method).to.equal('GET')
    })

    it('writeSkillResource should PUT /:name/resource with body', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'sk1' }, body: { content: 'new' } })

      await writeSkillResource(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].method).to.equal('PUT')
      expect(stub.firstCall.args[0].data).to.deep.equal({ content: 'new' })
    })

    it('removeSkillResource should DELETE /:name/resource', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'sk1' } })

      await removeSkillResource(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].method).to.equal('DELETE')
      expect(stub.firstCall.args[0].data).to.be.undefined
    })
  })

  describe('candidate review handlers', () => {
    it('getPendingSkillCandidates should GET /candidates/pending', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: [] })

      await getPendingSkillCandidates(appConfig)(makeReq(), makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/candidates/pending')
    })

    it('approveSkillCandidate should POST /candidates/:id/approve', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { candidateId: 'c42' } })

      await approveSkillCandidate(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/candidates/c42/approve')
      expect(stub.firstCall.args[0].method).to.equal('POST')
    })

    it('rejectSkillCandidate should POST /candidates/:id/reject', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { candidateId: 'c42' } })

      await rejectSkillCandidate(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/candidates/c42/reject')
    })
  })

  describe('import handlers', () => {
    it('previewNpmSkillImport should POST /import/npm/preview', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: { preview: true } })
      const req = makeReq({ body: { package: '@scope/skill' } })

      await previewNpmSkillImport(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/import/npm/preview')
      expect(stub.firstCall.args[0].method).to.equal('POST')
    })

    it('previewUrlSkillImport should POST /import/url/preview', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ body: { url: 'https://example.com/skill.zip' } })

      await previewUrlSkillImport(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/import/url/preview')
    })

    it('finalizeSkillImport should POST /import/finalize', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 201, data: { name: 'imported' } })
      const res = makeRes()

      await finalizeSkillImport(appConfig)(makeReq({ body: { importId: 'i1' } }), res, sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/import/finalize')
      expect(res.status.calledWith(201)).to.be.true
    })
  })

  describe('deprecateSkill', () => {
    it('should POST /:name/deprecate', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({ params: { name: 'old-skill' } })

      await deprecateSkill(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/old-skill/deprecate')
      expect(stub.firstCall.args[0].method).to.equal('POST')
    })
  })

  describe('getSkillUsage', () => {
    it('should GET /:name/usage', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: { agents: 3 } })
      const res = makeRes()
      const req = makeReq({ params: { name: 'sk1' } })

      await getSkillUsage(appConfig)(req, res, sinon.stub())

      expect(stub.firstCall.args[0].url).to.include('/sk1/usage')
      expect(res.json.calledWith({ agents: 3 })).to.be.true
    })
  })

  // ===================== previewUploadSkillImport (bespoke) =====================

  describe('previewUploadSkillImport', () => {
    it('should return 400 when no file is uploaded', async () => {
      const res = makeRes()

      await previewUploadSkillImport(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(400)).to.be.true
      expect(res.json.firstCall.args[0].detail).to.include('required')
    })

    it('should forward file as multipart and return upstream JSON', async () => {
      const stub = sinon.stub(axios, 'post').resolves({
        status: 200,
        data: { skills: [{ name: 'imported' }] },
      })
      const req = makeReq({
        file: {
          buffer: Buffer.from('PK-zip-data'),
          originalname: 'skill.zip',
          mimetype: 'application/zip',
        },
        body: {},
      })
      const res = makeRes()

      await previewUploadSkillImport(appConfig)(req, res, sinon.stub())

      expect(stub.calledOnce).to.be.true
      expect(stub.firstCall.args[0]).to.include('/import/upload/preview')
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ skills: [{ name: 'imported' }] })).to.be.true
    })

    it('should forward upstream error status', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 422,
        data: { detail: 'invalid skill format' },
      })
      const req = makeReq({
        file: { buffer: Buffer.from('bad'), originalname: 'bad.zip', mimetype: 'application/zip' },
        body: {},
      })
      const res = makeRes()

      await previewUploadSkillImport(appConfig)(req, res, sinon.stub())

      expect(res.status.calledWith(422)).to.be.true
    })

    it('should call next on network error', async () => {
      sinon.stub(axios, 'post').rejects({ code: 'ECONNREFUSED' })
      const req = makeReq({
        file: { buffer: Buffer.from('z'), originalname: 'a.zip', mimetype: 'application/zip' },
        body: {},
      })
      const next = sinon.stub()

      await previewUploadSkillImport(appConfig)(req, makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('unavailable')
    })

    it('should use default filename when originalname is empty', async () => {
      sinon.stub(axios, 'post').resolves({ status: 200, data: {} })
      const req = makeReq({
        file: { buffer: Buffer.from('z'), originalname: '', mimetype: '' },
        body: {},
      })

      await previewUploadSkillImport(appConfig)(req, makeRes(), sinon.stub())
    })

    it('should strip original content-type headers', async () => {
      const stub = sinon.stub(axios, 'post').resolves({ status: 200, data: {} })
      const req = makeReq({
        headers: {
          'content-type': 'multipart/form-data; boundary=orig',
          authorization: 'Bearer tok',
        },
        file: { buffer: Buffer.from('z'), originalname: 'a.zip', mimetype: 'application/zip' },
        body: {},
      })

      await previewUploadSkillImport(appConfig)(req, makeRes(), sinon.stub())

      const passedHeaders = stub.firstCall.args[2]?.headers as Record<string, string>
      expect(passedHeaders['content-type']).to.include('multipart/form-data')
      expect(passedHeaders['content-type']).to.not.include('boundary=orig')
    })
  })

  // ===================== exportSkill (bespoke) =====================

  describe('exportSkill', () => {
    it('should return markdown content with proper headers', async () => {
      sinon.stub(axios, 'get').resolves({
        status: 200,
        data: '# My Skill\nDoes stuff.',
        headers: {
          'content-type': 'text/markdown',
          'content-disposition': 'attachment; filename="my-skill.md"',
        },
      })
      const req = makeReq({ params: { name: 'my-skill' } })
      const res = makeRes()

      await exportSkill(appConfig)(req, res, sinon.stub())

      expect(res.status.calledWith(200)).to.be.true
      expect(res.setHeader.calledWith('Content-Type', 'text/markdown')).to.be.true
      expect(res.setHeader.calledWith('Content-Disposition', 'attachment; filename="my-skill.md"')).to.be.true
      expect(res.send.calledWith('# My Skill\nDoes stuff.')).to.be.true
    })

    it('should default content-type to text/markdown when absent', async () => {
      sinon.stub(axios, 'get').resolves({
        status: 200,
        data: '# Skill',
        headers: {},
      })
      const res = makeRes()

      await exportSkill(appConfig)(makeReq({ params: { name: 's' } }), res, sinon.stub())

      expect(res.setHeader.calledWith('Content-Type', 'text/markdown')).to.be.true
    })

    it('should not set Content-Disposition when absent from upstream', async () => {
      sinon.stub(axios, 'get').resolves({
        status: 200,
        data: '# S',
        headers: { 'content-type': 'text/plain' },
      })
      const res = makeRes()

      await exportSkill(appConfig)(makeReq({ params: { name: 's' } }), res, sinon.stub())

      const setHeaderCalls = res.setHeader.getCalls().map((c: any) => c.args[0])
      expect(setHeaderCalls).to.not.include('Content-Disposition')
    })

    it('should forward upstream error status', async () => {
      sinon.stub(axios, 'get').resolves({ status: 404, data: 'Not found', headers: {} })
      const res = makeRes()

      await exportSkill(appConfig)(makeReq({ params: { name: 'ghost' } }), res, sinon.stub())

      expect(res.status.calledWith(404)).to.be.true
    })

    it('should call next on network error', async () => {
      sinon.stub(axios, 'get').rejects({ code: 'ETIMEDOUT' })
      const next = sinon.stub()

      await exportSkill(appConfig)(makeReq({ params: { name: 's' } }), makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('unavailable')
    })

    it('should URL-encode skill name in export path', async () => {
      const stub = sinon.stub(axios, 'get').resolves({ status: 200, data: '', headers: {} })
      const req = makeReq({ params: { name: 'my skill/v2' } })

      await exportSkill(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0]).to.include('my%20skill%2Fv2/export')
    })

    it('should forward with mapAxiosError on upstream response error', async () => {
      sinon.stub(axios, 'get').rejects({
        response: { status: 500, data: { detail: 'internal' } },
      })
      const next = sinon.stub()

      await exportSkill(appConfig)(makeReq({ params: { name: 's' } }), makeRes(), next)

      expect(next.firstCall.args[0].message).to.include('internal')
    })
  })

  // ===================== header forwarding (via forwardJson) =====================

  describe('header forwarding', () => {
    it('should strip hop-by-hop headers', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({
        headers: {
          authorization: 'Bearer tok',
          host: 'evil.com',
          connection: 'keep-alive',
          'accept-encoding': 'gzip',
          'x-custom': 'kept',
        },
      })

      await listSkills(appConfig)(req, makeRes(), sinon.stub())

      const headers = stub.firstCall.args[0].headers
      expect(headers).to.not.have.property('host')
      expect(headers).to.not.have.property('connection')
      expect(headers).to.not.have.property('accept-encoding')
      expect(headers).to.have.property('x-custom', 'kept')
    })

    it('should join array header values', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({
        headers: { 'x-multi': ['a', 'b'], authorization: 'Bearer tok' },
      })

      await listSkills(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].headers['x-multi']).to.equal('a, b')
    })

    it('should skip null/undefined header values', async () => {
      const stub = sinon.stub(axios, 'request').resolves({ status: 200, data: {} })
      const req = makeReq({
        headers: { 'x-null': null, authorization: 'Bearer tok' },
      })

      await listSkills(appConfig)(req, makeRes(), sinon.stub())

      expect(stub.firstCall.args[0].headers).to.not.have.property('x-null')
    })
  })
})
