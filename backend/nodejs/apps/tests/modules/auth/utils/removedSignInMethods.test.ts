import { expect } from 'chai';
import {
  StoredAuthStep,
  withoutRemovedSignInMethods,
} from '../../../../src/modules/auth/utils/removedSignInMethods';
import { useCommunitySignInLists, useSignInLists } from '../../../helpers/sign-in-lists';

const step = (order: number, ...types: string[]): StoredAuthStep => ({
  order,
  allowedMethods: types.map((type) => ({ type })),
});

describe('withoutRemovedSignInMethods', () => {
  useCommunitySignInLists();

  it('leaves a policy without removed methods unchanged', () => {
    const stored = [step(1, 'password', 'google'), step(2, 'otp')];
    const result = withoutRemovedSignInMethods(stored);
    expect(result.changed).to.equal(false);
    expect(result.removed).to.deep.equal([]);
    expect(result.steps).to.deep.equal(stored);
  });

  it('turns a SAML-only policy into password sign-in', () => {
    const result = withoutRemovedSignInMethods([step(1, 'samlSso')]);
    expect(result.steps).to.deep.equal([step(1, 'password')]);
    expect(result.removed).to.deep.equal(['samlSso']);
    expect(result.changed).to.equal(true);
  });

  it('adds password next to the methods that remain', () => {
    const result = withoutRemovedSignInMethods([step(1, 'google', 'samlSso')]);
    expect(result.steps).to.deep.equal([step(1, 'google', 'password')]);
    expect(result.removed).to.deep.equal(['samlSso']);
  });

  it('does not add password when a later step already offers it', () => {
    const result = withoutRemovedSignInMethods([
      step(1, 'google', 'oauth'),
      step(2, 'password'),
    ]);
    expect(result.steps).to.deep.equal([step(1, 'google'), step(2, 'password')]);
    expect(result.removed).to.deep.equal(['oauth']);
  });

  it('drops a step left empty and renumbers the rest', () => {
    const result = withoutRemovedSignInMethods([
      step(1, 'password'),
      step(2, 'oauth'),
      step(3, 'otp'),
    ]);
    expect(result.steps).to.deep.equal([step(1, 'password'), step(2, 'otp')]);
    expect(result.changed).to.equal(true);
  });

  it('renames Azure AD to Microsoft without counting it as removed', () => {
    const result = withoutRemovedSignInMethods([step(1, 'password', 'azureAd')]);
    expect(result.steps).to.deep.equal([step(1, 'password', 'microsoft')]);
    expect(result.removed).to.deep.equal([]);
    expect(result.changed).to.equal(true);
  });

  it('keeps one Microsoft when the policy already had it', () => {
    const result = withoutRemovedSignInMethods([
      step(1, 'microsoft', 'azureAd', 'password'),
    ]);
    expect(result.steps).to.deep.equal([step(1, 'microsoft', 'password')]);
  });

  it('sorts steps stored out of order', () => {
    const result = withoutRemovedSignInMethods([step(2, 'otp'), step(1, 'password')]);
    expect(result.steps).to.deep.equal([step(1, 'password'), step(2, 'otp')]);
    expect(result.changed).to.equal(true);
  });

  for (const stored of [null, undefined, []]) {
    it(`gives password sign-in for ${JSON.stringify(stored) ?? 'undefined'}`, () => {
      const result = withoutRemovedSignInMethods(stored as StoredAuthStep[] | null);
      expect(result.steps).to.deep.equal([step(1, 'password')]);
      expect(result.removed).to.deep.equal([]);
      expect(result.changed).to.equal(true);
    });
  }

  it('treats a step with no method list as empty', () => {
    const result = withoutRemovedSignInMethods([
      { order: 1 } as unknown as StoredAuthStep,
      step(2, 'google'),
    ]);
    expect(result.steps).to.deep.equal([step(1, 'google')]);
  });

  it('lists removed methods once, in the order first seen', () => {
    const result = withoutRemovedSignInMethods([
      step(1, 'oauth', 'samlSso'),
      step(2, 'samlSso', 'oauth', 'otp'),
    ]);
    expect(result.removed).to.deep.equal(['oauth', 'samlSso']);
    expect(result.steps).to.deep.equal([step(1, 'otp', 'password')]);
  });
});

describe('withoutRemovedSignInMethods in an edition that removes nothing', () => {
  useSignInLists([], {});

  it('keeps SAML, OAuth and Azure AD steps as they are', () => {
    const stored = [step(1, 'samlSso', 'azureAd'), step(2, 'oauth', 'otp')];
    const result = withoutRemovedSignInMethods(stored);
    expect(result.steps).to.deep.equal(stored);
    expect(result.removed).to.deep.equal([]);
    expect(result.changed).to.equal(false);
  });
});
