import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { StatusBadge } from '../status-badge';

describe('StatusBadge', () => {
  afterEach(cleanup);

  it('labels a Slack-only account', () => {
    render(
      <Theme>
        <StatusBadge status="SlackOnly" />
      </Theme>,
    );
    expect(screen.getByText('Slack only')).toBeTruthy();
  });
});
