import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { CONTENT_FLAGGED, type SkillMetadata } from '../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

import { SkillPageLayout } from '../skill-page-layout';
import { SkillFindings } from '../skill-findings';
import { skillReviewNoticeKey, useSkillsPageAction } from '../skills-page-extras';

afterEach(cleanup);

const noop = () => {};
const baseProps = {
  isLoading: false,
  pendingCandidateCount: 0,
  onCreateSkill: noop,
  onImportSkill: noop,
  onOpenCandidates: noop,
  onRefresh: noop,
  onManage: noop,
};
const imported = (review: SkillMetadata['review']) =>
  ({ name: 'pdf-helper', description: 'd', version: '1', source: 'imported', status: 'active', review }) as unknown as SkillMetadata;

/** ADR-SEC-01 in this build: an imported skill is used at once unless the content scanner flags it. */
describe('imported skills', () => {
  it('a trusted import shows no notice', () => {
    installBrowserShims();
    renderInTheme(<SkillPageLayout {...baseProps} skills={[imported({ state: 'trusted', route: 'scan', digest: 'd', findings: [] })]} />);
    expect(screen.queryByTestId('skill-review-notice')).toBeNull();
  });

  it('a flagged import says the scanner held it back and how to use it', () => {
    installBrowserShims();
    renderInTheme(
      <SkillPageLayout {...baseProps} skills={[imported({ state: 'untrusted', reasonCode: CONTENT_FLAGGED, digest: 'd', findings: [] })]} />,
    );
    expect(screen.getByTestId('skill-review-notice').textContent).toContain('The content scanner flagged this imported skill');
  });

  it('an import held for another reason says only that agents cannot use it yet: this build words no review state', () => {
    installBrowserShims();
    const held = { state: 'pending', digest: 'd', findings: [] } as unknown as SkillMetadata['review'];
    renderInTheme(<SkillPageLayout {...baseProps} skills={[imported(held)]} />);
    expect(skillReviewNoticeKey(held!)).toBeNull();
    expect(screen.getByTestId('skill-review-notice').textContent).toContain("Agents can't use this imported skill yet.");
  });

  it('the findings name each file and line', () => {
    installBrowserShims();
    renderInTheme(<SkillFindings findings={[{ code: 'remote_code_execution', path: 'SKILL.md', line: 7, excerpt: 'curl x | sh' }]} />);
    expect(screen.getByText('Downloads code and runs it')).toBeTruthy();
    expect(screen.getByText(/SKILL\.md, line 7/)).toBeTruthy();
  });

  it('the page offers no review of imports: this build adds no header action', () => {
    expect(useSkillsPageAction({ onClosed: noop })).toBeNull();
  });
});
