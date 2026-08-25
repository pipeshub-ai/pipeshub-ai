/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'

import {
  getSpeechCapabilities,
  synthesizeSpeech,
  transcribeAudio,
} from '../../../../src/modules/enterprise_search/controller/speech.controller'

function makeReq(overrides: Record<string, any> = {}): any {
  return {
    headers: {
      authorization: 'Bearer tok123',
      'content-type': 'application/json',
      host: 'localhost',
      connection: 'keep-alive',
      'accept-encoding': 'gzip',
      'x-custom': 'kept',
    },
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

describe('SpeechController', () => {
  afterEach(() => sinon.restore())

  // ===================== getSpeechCapabilities =====================

  describe('getSpeechCapabilities', () => {
    it('should forward upstream JSON on success', async () => {
      const stub = sinon.stub(axios, 'get').resolves({
        status: 200,
        data: { tts: true, stt: false },
      })

      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(req, res, next)

      expect(stub.calledOnce).to.be.true
      expect(stub.firstCall.args[0]).to.equal('http://ai:8000/api/v1/chat/speech/capabilities')
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ tts: true, stt: false })).to.be.true
      expect(next.called).to.be.false
    })

    it('should forward non-200 status from upstream', async () => {
      sinon.stub(axios, 'get').resolves({ status: 409, data: { detail: 'no provider' } })
      const res = makeRes()
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), res, next)

      expect(res.status.calledWith(409)).to.be.true
      expect(res.json.calledWith({ detail: 'no provider' })).to.be.true
    })

    it('should call next with BadGatewayError on upstream response error', async () => {
      sinon.stub(axios, 'get').rejects({
        response: { status: 500, data: { detail: 'broken' } },
      })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err.message).to.include('broken')
    })

    it('should call next with ServiceUnavailableError on network error', async () => {
      sinon.stub(axios, 'get').rejects({ code: 'ECONNREFUSED', message: 'connect fail' })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err.message).to.include('unavailable')
    })

    it('should strip hop-by-hop headers and keep custom headers', async () => {
      const stub = sinon.stub(axios, 'get').resolves({ status: 200, data: {} })
      const req = makeReq()

      await getSpeechCapabilities(appConfig)(req, makeRes(), sinon.stub())

      const headers = stub.firstCall.args[1]?.headers as Record<string, string>
      expect(headers).to.not.have.property('host')
      expect(headers).to.not.have.property('connection')
      expect(headers).to.not.have.property('accept-encoding')
      expect(headers).to.have.property('x-custom', 'kept')
      expect(headers).to.have.property('authorization', 'Bearer tok123')
    })

    it('should handle array header values by joining them', async () => {
      const stub = sinon.stub(axios, 'get').resolves({ status: 200, data: {} })
      const req = makeReq({ headers: { 'x-multi': ['a', 'b'], authorization: 'Bearer t' } })

      await getSpeechCapabilities(appConfig)(req, makeRes(), sinon.stub())

      const headers = stub.firstCall.args[1]?.headers as Record<string, string>
      expect(headers['x-multi']).to.equal('a, b')
    })

    it('should skip null/undefined header values', async () => {
      const stub = sinon.stub(axios, 'get').resolves({ status: 200, data: {} })
      const req = makeReq({ headers: { 'x-null': null, 'x-undef': undefined, authorization: 'Bearer t' } })

      await getSpeechCapabilities(appConfig)(req, makeRes(), sinon.stub())

      const headers = stub.firstCall.args[1]?.headers as Record<string, string>
      expect(headers).to.not.have.property('x-null')
      expect(headers).to.not.have.property('x-undef')
    })
  })

  // ===================== synthesizeSpeech =====================

  describe('synthesizeSpeech', () => {
    it('should stream audio buffer back on success', async () => {
      const audioData = Buffer.from('fake-audio')
      sinon.stub(axios, 'post').resolves({
        status: 200,
        data: audioData,
        headers: {
          'content-type': 'audio/mp3',
          'x-tts-provider': 'openai',
          'x-tts-model': 'tts-1',
        },
      })

      const req = makeReq({ body: { text: 'hello' } })
      const res = makeRes()
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(req, res, next)

      expect(res.status.calledWith(200)).to.be.true
      expect(res.setHeader.calledWith('Content-Type', 'audio/mp3')).to.be.true
      expect(res.setHeader.calledWith('Cache-Control', 'no-store')).to.be.true
      expect(res.setHeader.calledWith('X-TTS-Provider', 'openai')).to.be.true
      expect(res.setHeader.calledWith('X-TTS-Model', 'tts-1')).to.be.true
      expect(res.send.calledOnce).to.be.true
      expect(next.called).to.be.false
    })

    it('should use fallback content-type when upstream omits it', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 200,
        data: Buffer.from('audio'),
        headers: {},
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.setHeader.calledWith('Content-Type', 'application/octet-stream')).to.be.true
    })

    it('should not set x-tts headers when absent from response', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 200,
        data: Buffer.from('audio'),
        headers: { 'content-type': 'audio/wav' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      const setHeaderCalls = res.setHeader.getCalls().map((c: any) => c.args[0])
      expect(setHeaderCalls).to.not.include('X-TTS-Provider')
      expect(setHeaderCalls).to.not.include('X-TTS-Model')
    })

    it('should forward error JSON when upstream returns >= 400', async () => {
      const errBody = Buffer.from(JSON.stringify({ detail: 'text too long' }))
      sinon.stub(axios, 'post').resolves({
        status: 400,
        data: errBody,
        headers: { 'content-type': 'application/json' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(400)).to.be.true
      expect(res.json.calledOnce).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ detail: 'text too long' })
    })

    it('should handle upstream error with plain text body', async () => {
      const errBody = Buffer.from('Internal Server Error')
      sinon.stub(axios, 'post').resolves({
        status: 500,
        data: errBody,
        headers: { 'content-type': 'text/plain' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(500)).to.be.true
      expect(res.json.firstCall.args[0]).to.deep.equal({ detail: 'Internal Server Error' })
    })

    it('should handle upstream error with empty body', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 502,
        data: Buffer.alloc(0),
        headers: { 'content-type': 'application/json' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(502)).to.be.true
      expect(res.json.firstCall.args[0]).to.have.property('detail').that.includes('502')
    })

    it('should handle upstream error with malformed JSON', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 500,
        data: Buffer.from('{bad json'),
        headers: { 'content-type': 'application/json' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.json.firstCall.args[0]).to.have.property('detail').that.includes('500')
    })

    it('should call next on network error', async () => {
      sinon.stub(axios, 'post').rejects({ code: 'ETIMEDOUT' })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('unavailable')
    })

    it('should use req.body when present', async () => {
      const stub = sinon.stub(axios, 'post').resolves({
        status: 200,
        data: Buffer.from('a'),
        headers: {},
      })
      const body = { text: 'hello', voice: 'alloy' }

      await synthesizeSpeech(appConfig)(makeReq({ body }), makeRes(), sinon.stub())

      expect(stub.firstCall.args[1]).to.deep.equal(body)
    })

    it('should default to empty object when req.body is undefined', async () => {
      const stub = sinon.stub(axios, 'post').resolves({
        status: 200,
        data: Buffer.from('a'),
        headers: {},
      })

      await synthesizeSpeech(appConfig)(makeReq({ body: undefined }), makeRes(), sinon.stub())

      expect(stub.firstCall.args[1]).to.deep.equal({})
    })

    it('should map upstream error with data.message fallback', async () => {
      sinon.stub(axios, 'post').rejects({
        response: { status: 503, data: { message: 'rate limited' } },
      })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.firstCall.args[0].message).to.include('rate limited')
    })

    it('should map upstream error with non-object data', async () => {
      sinon.stub(axios, 'post').rejects({
        response: { status: 500, data: 'plain string' },
      })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.firstCall.args[0].message).to.include('upstream 500')
    })

    it('should map upstream error with null data', async () => {
      sinon.stub(axios, 'post').rejects({
        response: { status: 500, data: null },
      })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.firstCall.args[0].message).to.include('upstream 500')
    })
  })

  // ===================== transcribeAudio =====================

  describe('transcribeAudio', () => {
    it('should return 400 when file is missing', async () => {
      const req = makeReq()
      const res = makeRes()
      const next = sinon.stub()

      await transcribeAudio(appConfig)(req, res, next)

      expect(res.status.calledWith(400)).to.be.true
      expect(res.json.calledOnce).to.be.true
      expect(res.json.firstCall.args[0].message).to.include('required')
    })

    it('should forward file to upstream and return JSON', async () => {
      const stub = sinon.stub(axios, 'post').resolves({
        status: 200,
        data: { text: 'hello world' },
      })
      const req = makeReq({
        file: {
          buffer: Buffer.from('audio-data'),
          originalname: 'recording.wav',
          mimetype: 'audio/wav',
        },
        body: {},
      })
      const res = makeRes()
      const next = sinon.stub()

      await transcribeAudio(appConfig)(req, res, next)

      expect(stub.calledOnce).to.be.true
      expect(stub.firstCall.args[0]).to.equal('http://ai:8000/api/v1/chat/transcribe')
      expect(res.status.calledWith(200)).to.be.true
      expect(res.json.calledWith({ text: 'hello world' })).to.be.true
      expect(next.called).to.be.false
    })

    it('should include language when provided in body', async () => {
      const stub = sinon.stub(axios, 'post').resolves({
        status: 200,
        data: { text: 'hola' },
      })
      const req = makeReq({
        file: {
          buffer: Buffer.from('audio'),
          originalname: 'a.wav',
          mimetype: 'audio/wav',
        },
        body: { language: 'es' },
      })

      await transcribeAudio(appConfig)(req, makeRes(), sinon.stub())

      // The form data is the second arg; language should be appended
      expect(stub.calledOnce).to.be.true
    })

    it('should not include language when body.language is not a string', async () => {
      sinon.stub(axios, 'post').resolves({ status: 200, data: {} })
      const req = makeReq({
        file: { buffer: Buffer.from('a'), originalname: 'a.wav', mimetype: 'audio/wav' },
        body: { language: 42 },
      })

      await transcribeAudio(appConfig)(req, makeRes(), sinon.stub())
      // No error — just verifying it doesn't crash
    })

    it('should use default filename when originalname is empty', async () => {
      sinon.stub(axios, 'post').resolves({ status: 200, data: {} })
      const req = makeReq({
        file: { buffer: Buffer.from('a'), originalname: '', mimetype: '' },
        body: {},
      })

      await transcribeAudio(appConfig)(req, makeRes(), sinon.stub())
    })

    it('should forward upstream error status', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 413,
        data: { detail: 'file too large' },
      })
      const req = makeReq({
        file: { buffer: Buffer.from('a'), originalname: 'a.wav', mimetype: 'audio/wav' },
        body: {},
      })
      const res = makeRes()

      await transcribeAudio(appConfig)(req, res, sinon.stub())

      expect(res.status.calledWith(413)).to.be.true
    })

    it('should call next on network error', async () => {
      sinon.stub(axios, 'post').rejects({ code: 'ECONNREFUSED' })
      const req = makeReq({
        file: { buffer: Buffer.from('a'), originalname: 'a.wav', mimetype: 'audio/wav' },
        body: {},
      })
      const next = sinon.stub()

      await transcribeAudio(appConfig)(req, makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('unavailable')
    })

    it('should replace original content-type with form-data boundary', async () => {
      const stub = sinon.stub(axios, 'post').resolves({ status: 200, data: {} })
      const req = makeReq({
        headers: {
          'content-type': 'multipart/form-data; boundary=abc',
          authorization: 'Bearer tok',
        },
        file: { buffer: Buffer.from('a'), originalname: 'a.wav', mimetype: 'audio/wav' },
        body: {},
      })

      await transcribeAudio(appConfig)(req, makeRes(), sinon.stub())

      const passedHeaders = stub.firstCall.args[2]?.headers as Record<string, string>
      // The original content-type is deleted; form-data library sets its own
      expect(passedHeaders['content-type']).to.include('multipart/form-data')
      expect(passedHeaders['content-type']).to.not.include('boundary=abc')
    })
  })
})
