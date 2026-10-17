import 'reflect-metadata';
import { expect } from 'chai';
import type { Request } from 'express';
import {
  canRevealSecrets,
  isSecretRevealAvailable,
} from '../../../../src/modules/configuration_manager/utils/secretReveal';

const request = (query: Record<string, unknown>, user: Record<string, unknown> = {}) =>
  ({ query, user }) as unknown as Request;

describe('secret reveal gate', () => {
  it('is always available: this edition holds one org', () => {
    expect(isSecretRevealAvailable()).to.equal(true);
  });

  it('reveals only when the request asks for it', () => {
    expect(canRevealSecrets(request({ reveal: 'true' }))).to.equal(true);
    expect(canRevealSecrets(request({}))).to.equal(false);
    expect(canRevealSecrets(request({ reveal: '1' }))).to.equal(false);
  });

  it('refuses OAuth-app tokens', () => {
    expect(canRevealSecrets(request({ reveal: 'true' }, { isOAuth: true }))).to.equal(false);
  });
});
