/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { jwtValidator } from '../../../../src/modules/mail/middlewares/userAuthentication'
import { ContainerRequest } from '../../../../src/modules/auth/middlewares/types'
import { Response, NextFunction } from 'express'

describe('mail jwtValidator middleware', () => {
  let req: Partial<ContainerRequest>
  let res: Partial<Response>
  let next: sinon.SinonStub

  beforeEach(() => {
    req = {
      header: sinon.stub(),
    } as any
    res = {}
    next = sinon.stub()
  })

  afterEach(() => {
    sinon.restore()
  })

  it('should call next with NotFoundError when container is missing', () => {
    req.container = undefined
    jwtValidator(req as ContainerRequest, res as Response, next as NextFunction)
    expect(next.calledOnce).to.be.true
    const err = next.firstCall.args[0]
    expect(err).to.be.instanceOf(Error)
    expect(err.message).to.include('Mail container not found')
  })

  it('should call next with error when token is invalid', () => {
    const mockContainer = {
      get: sinon.stub().returns({ jwtSecret: 'test-secret' }),
    }
    req.container = mockContainer as any
    ;(req.header as sinon.SinonStub).withArgs('authorization').returns('Bearer invalid-token')

    jwtValidator(req as ContainerRequest, res as Response, next as NextFunction)

    expect(next.calledOnce).to.be.true
    const err = next.firstCall.args[0]
    expect(err).to.be.instanceOf(Error)
  })

  it('should call next with error when authorization header is missing', () => {
    const mockContainer = {
      get: sinon.stub().returns({ jwtSecret: 'test-secret' }),
    }
    req.container = mockContainer as any
    ;(req.header as sinon.SinonStub).returns(undefined)

    jwtValidator(req as ContainerRequest, res as Response, next as NextFunction)

    expect(next.calledOnce).to.be.true
    const err = next.firstCall.args[0]
    expect(err).to.be.instanceOf(Error)
    expect(err.message).to.include('Authorization header not found')
  })
})
