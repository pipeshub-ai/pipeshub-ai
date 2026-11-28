import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, renderHook, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const prompt = vi.hoisted(() => ({ admin: true }));
vi.mock('@/lib/hooks/use-enterprise-prompt', () => ({ useEnterprisePrompt: () => prompt.admin }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { KbSelectionActions } from '../components/kb-selection-actions';
import { useKbUploadExtras } from '../hooks/use-kb-upload-extras';
import { ConnectorOverviewExtras } from '@/app/(main)/workspace/connectors/components/instance-panel/connector-overview-extras';

const wrap = (ui: React.ReactElement) => render(<Theme>{ui}</Theme>);
const BADGE = 'Available in Enterprise';

afterEach(() => {
  cleanup();
  prompt.admin = true;
});

describe('default extension slots', () => {
  it('shows the selection-bar notice with the badge to admins, and nothing to members', () => {
    wrap(<KbSelectionActions selectedItems={[]} pageViewMode="collections" />);
    expect(screen.getByTestId('kb-selection-actions-notice')).toBeTruthy();
    expect(screen.getByText(BADGE)).toBeTruthy();
    cleanup();
    prompt.admin = false;
    const { container } = wrap(<KbSelectionActions selectedItems={[]} pageViewMode="collections" />);
    expect(screen.queryByTestId('kb-selection-actions-notice')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('offers an upload panel with the badge to admins, a null panel to members, and no callbacks', () => {
    const admin = renderHook(() => useKbUploadExtras('kb-1')).result.current;
    expect(admin.panel).not.toBeNull();
    expect(admin.onUploadStart).toBeUndefined();
    expect(admin.onFileSucceeded).toBeUndefined();
    expect(admin.onUploadEnd).toBeUndefined();
    wrap(<>{admin.panel}</>);
    expect(screen.getByTestId('kb-upload-extras-notice')).toBeTruthy();
    expect(screen.getByText(BADGE)).toBeTruthy();
    cleanup();

    prompt.admin = false;
    expect(renderHook(() => useKbUploadExtras(null)).result.current.panel).toBeNull();
  });

  it('shows the connector overview notice with the badge to admins, and nothing to members', () => {
    wrap(<ConnectorOverviewExtras connectorId="c1" refreshKey={0} />);
    expect(screen.getByTestId('indexing-priority-notice')).toBeTruthy();
    expect(screen.getByText(BADGE)).toBeTruthy();
    cleanup();
    prompt.admin = false;
    const { container } = wrap(<ConnectorOverviewExtras connectorId="c1" refreshKey={0} />);
    expect(container.textContent).toBe('');
  });
});
