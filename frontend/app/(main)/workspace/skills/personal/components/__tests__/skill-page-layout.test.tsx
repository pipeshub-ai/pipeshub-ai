import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { SkillMetadata } from '../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { SkillPageLayout } from '../skill-page-layout';

afterEach(cleanup);

const noop = () => {};
const baseProps = {
  skills: [] as SkillMetadata[],
  isLoading: false,
  pendingCandidateCount: 0,
  onCreateSkill: noop,
  onImportSkill: noop,
  onOpenCandidates: noop,
  onRefresh: noop,
  onManage: noop,
};

describe('SkillPageLayout', () => {
  it('shows the load error with a retry instead of the empty state', () => {
    installBrowserShims();
    const onRefresh = vi.fn();
    renderInTheme(<SkillPageLayout {...baseProps} error="Failed to load skills" onRefresh={onRefresh} />);
    expect(screen.getByText('Failed to load skills')).toBeTruthy();
    expect(screen.queryByText('No skills yet')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('keeps cached cards and still shows a failed refresh with a retry', () => {
    installBrowserShims();
    const onRefresh = vi.fn();
    const skill = { name: 'cached-skill', description: 'd', version: '1', source: 'manual', status: 'active' } as unknown as SkillMetadata;
    renderInTheme(<SkillPageLayout {...baseProps} skills={[skill]} error="Failed to load skills" onRefresh={onRefresh} />);
    expect(screen.getByText('Failed to load skills')).toBeTruthy();
    expect(screen.getByText('cached-skill')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('labels the availability switch on each card', () => {
    installBrowserShims();
    const skill = {
      name: 't5-skill',
      description: 'd',
      version: '1',
      source: 'manual',
      status: 'active',
    } as unknown as SkillMetadata;
    renderInTheme(<SkillPageLayout {...baseProps} skills={[skill]} onToggleAvailability={noop} />);
    expect(screen.getByText('Available')).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Available to agents' })).toBeTruthy();
  });
});
