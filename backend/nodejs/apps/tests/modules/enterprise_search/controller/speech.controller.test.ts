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

    it('should hide a 5xx service message, which is the path that happens', async () => {
      // validateStatus accepts every status, so axios resolves here rather
      // than throwing: this is the reply a reader actually receives.
      sinon.stub(axios, 'get').resolves({ status: 500, data: { detail: 'broken' } })
      const res = makeRes()

      await getSpeechCapabilities(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(500)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(JSON.stringify(body)).to.not.include('broken')
      expect(body.detail).to.include('check the speech settings')
    })

    it('should keep a resolved 4xx message and its status', async () => {
      sinon.stub(axios, 'get').resolves({
        status: 409,
        data: { detail: 'No speech provider is set up yet.' },
      })
      const res = makeRes()

      await getSpeechCapabilities(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(409)).to.be.true
      expect(res.json.firstCall.args[0].detail).to.equal('No speech provider is set up yet.')
    })

    it('should hide a 5xx service message behind plain advice', async () => {
      sinon.stub(axios, 'get').rejects({
        response: { status: 500, data: { detail: 'broken' } },
      })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err.message).to.equal(
        'Something went wrong while PipesHub tried to check the speech settings. ' +
          'Please try again in a moment; if it keeps happening, ask your admin to check the services page.',
      )
      expect(err.message).to.not.include('broken')
    })

    it('should keep an unmapped thrown 4xx status, such as 413', async () => {
      // An oversized recording is the likely 413 here; 400 would tell the
      // caller the wrong thing about what to do next.
      sinon.stub(axios, 'get').rejects({
        response: { status: 413, data: { detail: 'That recording is too large.' } },
      })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      const err = next.firstCall.args[0]
      expect(err.statusCode).to.equal(413)
      expect(err.message).to.equal('That recording is too large.')
    })

    it('should keep a thrown 4xx status even when the service sent no words', async () => {
      sinon.stub(axios, 'get').rejects({ response: { status: 404, data: {} } })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      const err = next.firstCall.args[0]
      expect(err.statusCode).to.equal(404)
      expect(err.message).to.include('check the speech settings')
    })

    it('should keep a thrown 4xx message and its status, not flatten it to 502', async () => {
      sinon.stub(axios, 'get').rejects({
        response: { status: 409, data: { detail: 'No speech provider is set up yet.' } },
      })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      const err = next.firstCall.args[0]
      expect(err.message).to.equal('No speech provider is set up yet.')
      expect(err.statusCode).to.equal(409)
    })

    it('should call next with ServiceUnavailableError on network error', async () => {
      sinon.stub(axios, 'get').rejects({ code: 'ECONNREFUSED', message: 'connect fail' })
      const next = sinon.stub()

      await getSpeechCapabilities(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      const err = next.firstCall.args[0]
      expect(err.message).to.include('trouble reaching one of its services')
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

    it('should hide a resolved 5xx service message behind plain advice', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 500,
        data: Buffer.from(JSON.stringify({ detail: 'broken' })),
        headers: { 'content-type': 'application/json' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.status.calledWith(500)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(JSON.stringify(body)).to.not.include('broken')
      expect(body.detail).to.include('read this message aloud')
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
      // The service's own words described its internals; the reader gets advice.
      const body = res.json.firstCall.args[0]
      expect(body.detail).to.include('read this message aloud')
      expect(JSON.stringify(body)).to.not.include('Internal Server Error')
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
      const body = res.json.firstCall.args[0]
      expect(body.detail).to.include('read this message aloud')
      expect(JSON.stringify(body)).to.not.include('502')
    })

    it('should handle upstream error with malformed JSON', async () => {
      sinon.stub(axios, 'post').resolves({
        status: 500,
        data: Buffer.from('{bad json'),
        headers: { 'content-type': 'application/json' },
      })
      const res = makeRes()

      await synthesizeSpeech(appConfig)(makeReq(), res, sinon.stub())

      expect(res.json.firstCall.args[0].detail).to.include('read this message aloud')
    })

    it('should call next on network error', async () => {
      sinon.stub(axios, 'post').rejects({ code: 'ETIMEDOUT' })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.calledOnce).to.be.true
      expect(next.firstCall.args[0].message).to.include('trouble reaching one of its services')
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

    it('should hide a 5xx service message behind plain advice', async () => {
      sinon.stub(axios, 'post').rejects({
        response: { status: 503, data: { message: 'rate limited' } },
      })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      const message = next.firstCall.args[0].message
      expect(message).to.include('read this message aloud')
      expect(message).to.not.include('rate limited')
    })

    it('should map upstream error with non-object data', async () => {
      sinon.stub(axios, 'post').rejects({
        response: { status: 500, data: 'plain string' },
      })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.firstCall.args[0].message).to.include('read this message aloud')
    })

    it('should map upstream error with null data', async () => {
      sinon.stub(axios, 'post').rejects({
        response: { status: 500, data: null },
      })
      const next = sinon.stub()

      await synthesizeSpeech(appConfig)(makeReq(), makeRes(), next)

      expect(next.firstCall.args[0].message).to.include('read this message aloud')
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

    it('should hide a resolved 5xx service message behind plain advice', async () => {
      sinon.stub(axios, 'post').resolves({ status: 500, data: { detail: 'broken' } })
      const req = makeReq({
        file: { buffer: Buffer.from('a'), originalname: 'a.wav', mimetype: 'audio/wav' },
        body: {},
      })
      const res = makeRes()

      await transcribeAudio(appConfig)(req, res, sinon.stub())

      expect(res.status.calledWith(500)).to.be.true
      const body = res.json.firstCall.args[0]
      expect(JSON.stringify(body)).to.not.include('broken')
      expect(body.detail).to.include('turn your recording into text')
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
      expect(next.firstCall.args[0].message).to.include('trouble reaching one of its services')
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
