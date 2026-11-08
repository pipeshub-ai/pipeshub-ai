import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ColumnResizeHandle, useKbColumnWidths, type KbColumn } from '../components/kb-column-widths';

// jsdom has no PointerEvent; without it the pointer events below carry no button or clientX
if (typeof window.PointerEvent === 'undefined') {
  (window as unknown as { PointerEvent: typeof MouseEvent }).PointerEvent = class extends MouseEvent {};
}

function Harness({ left, right }: { left: KbColumn | 'name'; right: KbColumn | null }) {
  const { style, startResize, reset } = useKbColumnWidths();
  const vars = style as Record<string, string>;
  return (
    <div>
      <ColumnResizeHandle
        label="handle"
        onPointerDown={(e) => startResize(e, left, right)}
        onReset={() => reset([...(left === 'name' ? [] : [left]), ...(right ? [right] : [])])}
      />
      <output data-testid="w">{`${vars['--kb-col-size']} ${vars['--kb-col-created']} ${vars['--kb-col-updated']}`}</output>
    </div>
  );
}

const drag = (dx: number) => {
  const handle = screen.getByRole('separator', { name: 'handle' });
  fireEvent.pointerDown(handle, { button: 0, clientX: 100 });
  act(() => {
    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 100 + dx }));
    window.dispatchEvent(new PointerEvent('pointerup'));
  });
};

describe('records table column widths', () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it('moves only the boundary between two columns, and saves it', () => {
    render(<Harness left="size" right="created" />);
    drag(30);
    // size 96 -> 126 would leave created at 117, under its 120 minimum: the boundary stops at 123 / 120
    expect(screen.getByTestId('w').textContent).toBe('123px 120px 146px');
    expect(JSON.parse(window.localStorage.getItem('pipeshub.kb.columnWidths')!)).toMatchObject({ size: 123, created: 120 });

    cleanup();
    render(<Harness left="size" right="created" />);
    expect(screen.getByTestId('w').textContent).toBe('123px 120px 146px');
  });

  it("keeps each column above its own minimum, and double-click restores the defaults", () => {
    render(<Harness left="updated" right={null} />);
    drag(-500);
    // Updated stops at its 120px minimum, wide enough for "29 Oct 2026"
    expect(screen.getByTestId('w').textContent).toBe('96px 147px 120px');
    fireEvent.doubleClick(screen.getByRole('separator', { name: 'handle' }));
    expect(screen.getByTestId('w').textContent).toBe('96px 147px 146px');
  });
});
