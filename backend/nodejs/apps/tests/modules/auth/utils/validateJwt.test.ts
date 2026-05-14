import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { isJwtTokenValid } from '../../../../src/modules/auth/utils/validateJwt';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../../src/libs/errors/http.errors';

describe('isJwtTokenValid', () => {
  const privateKey = 'test-secret-key';

  afterEach(() => {
    sinon.restore();
  });

  it('should throw UnauthorizedError when jwt.verify returns a falsy payload', () => {
    const jwtMod = require('jsonwebtoken');
    sinon.stub(jwtMod, 'verify').returns(null as any);
    const req: any = {
      header: sinon.stub().withArgs('authorization').returns('Bearer some.jwt.token'),
    };

    expect(() => isJwtTokenValid(req, privateKey)).to.throw(
      UnauthorizedError,
      'Invalid Token',
    );
  });

  it('should return decoded data for a valid token', () => {
    const payload = { userId: 'u1', orgId: 'o1' };
    const token = jwt.sign(payload, privateKey, { expiresIn: '1h' });
    const req: any = {
      header: sinon.stub().withArgs('authorization').returns(`Bearer ${token}`),
    };

    const result = isJwtTokenValid(req, privateKey);
    expect(result.userId).to.equal('u1');
    expect(result.orgId).to.equal('o1');
    expect(result.jwtAuthToken).to.equal(token);
  });

  it('should throw BadRequestError when authorization header is missing', () => {
    const req: any = {
      header: sinon.stub().returns(undefined),
    };

    expect(() => isJwtTokenValid(req, privateKey)).to.throw(
      BadRequestError,
      'Authorization header not found',
    );
  });

  it('should throw BadRequestError when token is missing from header', () => {
    const req: any = {
      header: sinon.stub().withArgs('authorization').returns('Bearer'),
    };

    // "Bearer".split(' ') => ["Bearer"] -> bearer[1] is undefined
    expect(() => isJwtTokenValid(req, privateKey)).to.throw(BadRequestError);
  });

  it('should throw error for an expired token', () => {
    const token = jwt.sign({ userId: 'u1' }, privateKey, { expiresIn: '-1s' });
    const req: any = {
      header: sinon.stub().withArgs('authorization').returns(`Bearer ${token}`),
    };

    expect(() => isJwtTokenValid(req, privateKey)).to.throw();
  });

  it('should throw error for a token signed with a different secret', () => {
    const token = jwt.sign({ userId: 'u1' }, 'wrong-secret', {
      expiresIn: '1h',
    });
    const req: any = {
      header: sinon.stub().withArgs('authorization').returns(`Bearer ${token}`),
    };

    expect(() => isJwtTokenValid(req, privateKey)).to.throw();
  });

  it('should throw error for a malformed token', () => {
    const req: any = {
      header: sinon
        .stub()
        .withArgs('authorization')
        .returns('Bearer not-a-valid-jwt'),
    };

    expect(() => isJwtTokenValid(req, privateKey)).to.throw();
  });

  it('should attach jwtAuthToken to the decoded data', () => {
    const payload = { email: 'test@example.com' };
    const token = jwt.sign(payload, privateKey, { expiresIn: '1h' });
    const req: any = {
      header: sinon.stub().withArgs('authorization').returns(`Bearer ${token}`),
    };

    const result = isJwtTokenValid(req, privateKey);
    expect(result.jwtAuthToken).to.be.a('string');
    expect(result.jwtAuthToken).to.equal(token);
  });
});
