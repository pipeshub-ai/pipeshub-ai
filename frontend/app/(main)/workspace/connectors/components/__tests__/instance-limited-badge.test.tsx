import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { InstanceLimitedBadge } from '../instance-card/instance-status-badges';
import { installDomShims, makeInstance, renderInTheme, signInAs, signOut } from '../../__tests__/fixtures';

beforeEach(() => installDomShims());
afterEach(() => {
  cleanup();
  signOut();
});

describe('InstanceLimitedBadge', () => {
  it('marks a limited connector for an admin, whatever the limit', () => {
    signInAs('admin');
    for (const accessLimit of ['CREATOR', 'ORG', 'SOMETHING_ELSE']) {
      renderInTheme(<InstanceLimitedBadge instance={makeInstance({ accessLimit })} />);
      expect(screen.getByText('Limited')).toBeTruthy();
      cleanup();
    }
  });

  it('shows nothing on a connector without a limit', () => {
    signInAs('admin');
    renderInTheme(<InstanceLimitedBadge instance={makeInstance({ accessLimit: null })} />);
    renderInTheme(<InstanceLimitedBadge instance={makeInstance()} />);
    expect(screen.queryByText('Limited')).toBeNull();
  });

  it('shows nothing to a member, nor before the profile is known', () => {
    signInAs('member');
    renderInTheme(<InstanceLimitedBadge instance={makeInstance({ accessLimit: 'ORG' })} />);
    expect(screen.queryByText('Limited')).toBeNull();
    cleanup();
    signOut();
    renderInTheme(<InstanceLimitedBadge instance={makeInstance({ accessLimit: 'ORG' })} />);
    expect(screen.queryByText('Limited')).toBeNull();
  });
});
