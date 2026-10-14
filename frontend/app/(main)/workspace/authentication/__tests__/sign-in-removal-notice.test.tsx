import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import {
  SignInRemovalNotice,
  SIGN_IN_REMOVAL_VERSION,
  isMethodBeingRemoved,
  methodsBeingRemovedInUse,
} from '../components/sign-in-removal-notice';
import type { AuthMethodState, ConfigStatus } from '../types';

const nothingConfigured: ConfigStatus = { google: false, microsoft: false, samlSso: false, oauth: false };
const enabled = (...types: string[]): AuthMethodState[] =>
  ['password', 'otp', 'google', 'microsoft', 'samlSso', 'oauth'].map((type) => ({
    type,
    enabled: types.includes(type),
  })) as AuthMethodState[];

afterEach(cleanup);

describe('methodsBeingRemovedInUse', () => {
  it('is empty when the workspace signs in only with methods that stay', () => {
    expect(methodsBeingRemovedInUse(enabled('password', 'google', 'microsoft'), nothingConfigured)).toEqual([]);
  });

  it('includes a method that is turned on, even before it is set up', () => {
    expect(methodsBeingRemovedInUse(enabled('password', 'oauth'), nothingConfigured)).toEqual(['oauth']);
  });

  it('includes a method that is set up, even while it is turned off', () => {
    expect(methodsBeingRemovedInUse(enabled('password'), { ...nothingConfigured, samlSso: true })).toEqual(['samlSso']);
  });

  it('lists SAML before OAuth when both are in use', () => {
    expect(methodsBeingRemovedInUse(enabled('samlSso', 'oauth'), nothingConfigured)).toEqual(['samlSso', 'oauth']);
  });
});

describe('isMethodBeingRemoved', () => {
  it.each(['samlSso', 'oauth'])('is true for %s', (type) => {
    expect(isMethodBeingRemoved(type)).toBe(true);
  });

  it.each(['password', 'otp', 'google', 'microsoft'])('is false for %s, which stays', (type) => {
    expect(isMethodBeingRemoved(type)).toBe(false);
  });
});

describe('SignInRemovalNotice', () => {
  const renderNotice = (inUse: Parameters<typeof SignInRemovalNotice>[0]['inUse']) =>
    render(
      <Theme>
        <SignInRemovalNotice inUse={inUse} />
      </Theme>,
    );

  it('renders nothing when no method being removed is in use', () => {
    const { container } = renderNotice([]);
    expect(container.textContent).toBe('');
  });

  it('names the release and the methods the workspace uses', () => {
    renderNotice(['samlSso', 'oauth']);
    expect(screen.getByText(new RegExp(`removed in PipesHub ${SIGN_IN_REMOVAL_VERSION.replace(/\./g, '\\.')}`))).toBeTruthy();
    expect(screen.getByText(/This workspace uses SAML SSO and OAuth\./)).toBeTruthy();
  });
});
