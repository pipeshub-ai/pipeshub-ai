import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import { McpClientSecretPrefixHint, secretMissesPrefix } from '../mcp-client-secret-prefix-hint';

afterEach(() => {
  cleanup();
});

function renderHint(prefix: string | null | undefined, secret: string) {
  return render(
    <Theme>
      <McpClientSecretPrefixHint prefix={prefix} secret={secret} />
    </Theme>,
  );
}

describe('McpClientSecretPrefixHint', () => {
  it('says how the secret starts while nothing is entered', () => {
    renderHint('GOCSPX-', '');
    expect(screen.getByTestId('mcp-client-secret-prefix-hint').textContent).toBe(
      'Starts with GOCSPX-. Copy the whole secret, including that part.',
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('warns about a secret copied without its prefix', () => {
    // Live: a double-click copy stored the 28 characters after "GOCSPX-", and Google refused it.
    renderHint('GOCSPX-', 'aBcDeFgHiJkLmNoPqRsTuVwXyZ12');
    expect(screen.getByRole('alert').textContent).toContain("This doesn't start with GOCSPX-");
  });

  it('is quiet for a whole secret, and absent for providers without a prefix', () => {
    const { unmount } = renderHint('GOCSPX-', '  GOCSPX-aBcDeFgHiJkLmNoPqRsTuVwXyZ12 ');
    expect(screen.queryByRole('alert')).toBeNull();
    unmount();

    renderHint(null, 'anything');
    expect(screen.queryByTestId('mcp-client-secret-prefix-hint')).toBeNull();
  });
});

describe('secretMissesPrefix', () => {
  it.each([
    ['', 'GOCSPX-', false],
    ['GOCSPX-abc', 'GOCSPX-', false],
    ['abc', 'GOCSPX-', true],
    ['abc', null, false],
  ])('%j with prefix %j → %s', (secret, prefix, expected) => {
    expect(secretMissesPrefix(secret, prefix)).toBe(expected);
  });
});
