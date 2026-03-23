import { expect } from 'chai';
import { HttpMethod } from '../../../src/libs/enums/http-methods.enum';

describe('HttpMethod', () => {
  it('should have GET as "GET"', () => {
    expect(HttpMethod.GET).to.equal('GET');
  });

  it('should have POST as "POST"', () => {
    expect(HttpMethod.POST).to.equal('POST');
  });

  it('should have PUT as "PUT"', () => {
    expect(HttpMethod.PUT).to.equal('PUT');
  });

  it('should have DELETE as "DELETE"', () => {
    expect(HttpMethod.DELETE).to.equal('DELETE');
  });

  it('should have PATCH as "PATCH"', () => {
    expect(HttpMethod.PATCH).to.equal('PATCH');
  });

  it('should have HEAD as "HEAD"', () => {
    expect(HttpMethod.HEAD).to.equal('HEAD');
  });

  it('should have OPTIONS as "OPTIONS"', () => {
    expect(HttpMethod.OPTIONS).to.equal('OPTIONS');
  });

  it('should have CONNECT as "CONNECT"', () => {
    expect(HttpMethod.CONNECT).to.equal('CONNECT');
  });

  it('should have TRACE as "TRACE"', () => {
    expect(HttpMethod.TRACE).to.equal('TRACE');
  });

  it('should have exactly 9 methods', () => {
    expect(Object.keys(HttpMethod)).to.have.lengthOf(9);
  });

  it('should contain only the expected keys', () => {
    const expectedKeys = [
      'GET',
      'POST',
      'PUT',
      'DELETE',
      'PATCH',
      'HEAD',
      'OPTIONS',
      'CONNECT',
      'TRACE',
    ];
    expect(Object.keys(HttpMethod)).to.have.members(expectedKeys);
  });

  it('should be frozen (immutable)', () => {
    expect(Object.isFrozen(HttpMethod)).to.be.true;
  });
});
