import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { ToolsetToolRulesNotice } from '../components/toolset-tool-rules-notice';

// The Enterprise notices these screens show are for admins; render them as an admin would see them.
vi.mock('@/lib/hooks/use-enterprise-prompt', () => ({ useEnterprisePrompt: () => true }));

beforeEach(() => installBrowserShims());
afterEach(() => cleanup());

describe('ToolsetToolRulesNotice', () => {
  it("says what a toolset's tools do by default, and that organization rules aren't set here", () => {
    renderInTheme(
      <ToolsetToolRulesNotice open onOpenChange={() => {}} instanceId="inst-j" instanceName="Jira (Engineering)" toolsetType="jira" />
    );

    expect(screen.getByText('Tool approvals: Jira (Engineering)')).toBeTruthy();
    expect(screen.getByText(/A tool that deletes data, clears a sheet or sends email asks the person first/)).toBeTruthy();
    expect(screen.getByTestId('enterprise-feature-notice')).toBeTruthy();
    expect(screen.queryByRole('radio')).toBeNull();
  });
});
