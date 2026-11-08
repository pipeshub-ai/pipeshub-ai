import '@/lib/__tests__/test-i18n';
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { PEOPLE_SHOWN, SignInRemovalNotice } from '../components/sign-in-removal-notice';
import type { AuthMethodType, PersonWithoutPassword, SignInRemovalStatus } from '../types';

const alice: PersonWithoutPassword = { userId: 'u1', fullName: 'Alice Chen', email: 'alice@example.com' };
const bob: PersonWithoutPassword = { userId: 'u2', email: 'bob@example.com' };

afterEach(cleanup);

function renderNotice(status: SignInRemovalStatus | null, enabledMethods: AuthMethodType[] = ['password']) {
  return render(
    <Theme>
      <SignInRemovalNotice status={status} enabledMethods={enabledMethods} />
    </Theme>,
  );
}

describe('SignInRemovalNotice', () => {
  it('renders nothing before the status has loaded or when it could not load', () => {
    const { container } = renderNotice(null);
    expect(container.textContent).toBe('');
  });

  it('renders nothing when the upgrade removed no sign-in method', () => {
    const { container } = renderNotice({ removedMethods: [], peopleWithoutPassword: [alice] });
    expect(container.textContent).toBe('');
  });

  it('renders nothing once everyone has a password', () => {
    const { container } = renderNotice({ removedMethods: ['samlSso'], peopleWithoutPassword: [] });
    expect(container.textContent).toBe('');
  });

  it('names one removed method in the singular', () => {
    renderNotice({ removedMethods: ['samlSso'], peopleWithoutPassword: [alice] });
    expect(screen.getByText('SAML SSO sign-in was removed in PipesHub 1.0')).toBeTruthy();
  });

  it('names both removed methods in the plural', () => {
    renderNotice({ removedMethods: ['samlSso', 'oauth'], peopleWithoutPassword: [alice] });
    expect(screen.getByText('SAML SSO and OAuth sign-in were removed in PipesHub 1.0')).toBeTruthy();
  });

  it('says which methods the workspace signs in with now', () => {
    renderNotice({ removedMethods: ['oauth'], peopleWithoutPassword: [alice] }, ['password', 'google']);
    expect(screen.getByText(/This workspace now signs in with password and Google\./)).toBeTruthy();
  });

  it('lists people by name and email, or by email when there is no name', () => {
    renderNotice({ removedMethods: ['samlSso'], peopleWithoutPassword: [alice, bob] });
    expect(screen.getByText('Alice Chen (alice@example.com)')).toBeTruthy();
    expect(screen.getByText('bob@example.com')).toBeTruthy();
  });

  it(`shows ${PEOPLE_SHOWN} people and sums up the rest`, () => {
    const people = Array.from({ length: PEOPLE_SHOWN + 3 }, (_, i) => ({
      userId: `u${i}`,
      email: `person${i}@example.com`,
    }));
    renderNotice({ removedMethods: ['samlSso'], peopleWithoutPassword: people });
    expect(screen.getByText(`person${PEOPLE_SHOWN - 1}@example.com`)).toBeTruthy();
    expect(screen.queryByText(`person${PEOPLE_SHOWN}@example.com`)).toBeNull();
    expect(screen.getByText('and 3 more')).toBeTruthy();
  });

  it('tells the admin how those people get a password', () => {
    renderNotice({ removedMethods: ['samlSso'], peopleWithoutPassword: [alice] });
    expect(screen.getByText(/"Forgot Password" on the sign-in page/)).toBeTruthy();
    expect(screen.getByText(/create a set-password link/)).toBeTruthy();
  });
});
