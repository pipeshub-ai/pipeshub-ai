import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { renderInTheme } from '../../__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SidebarToolDragRow } from '../sidebar-draggable-row';

afterEach(cleanup);

function dragRow(label: string) {
  const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
  fireEvent.dragStart(screen.getByText(label).closest('[draggable]') as HTMLElement, { dataTransfer });
  return dataTransfer;
}

describe('SidebarToolDragRow', () => {
  it('hands every entry of its payload to the drop', () => {
    renderInTheme(<SidebarToolDragRow name="create_issue" data={{ type: 'tool', name: 'mcp_github_create_issue' }} />);

    const dataTransfer = dragRow('Create Issue');

    expect(dataTransfer.setData.mock.calls).toEqual([
      ['type', 'tool'],
      ['name', 'mcp_github_create_issue'],
    ]);
    expect(dataTransfer.effectAllowed).toBe('move');
  });

  it('a blocked row says why when pressed, and never drags', () => {
    const onBlocked = vi.fn();
    renderInTheme(<SidebarToolDragRow name="create_issue" data={{ type: 'tool' }} disabled onBlocked={onBlocked} />);
    const row = screen.getByText('Create Issue').closest('[draggable]') as HTMLElement;

    expect(row.getAttribute('draggable')).toBe('false');
    fireEvent.pointerDown(row);
    expect(onBlocked).toHaveBeenCalledOnce();

    const dataTransfer = dragRow('Create Issue');
    expect(dataTransfer.setData).not.toHaveBeenCalled();
  });

  it('an enabled row is dragged, not blocked, when pressed', () => {
    const onBlocked = vi.fn();
    renderInTheme(<SidebarToolDragRow name="create_issue" data={{ type: 'tool' }} onBlocked={onBlocked} />);

    fireEvent.pointerDown(screen.getByText('Create Issue').closest('[draggable]') as HTMLElement);
    expect(onBlocked).not.toHaveBeenCalled();
  });
});
