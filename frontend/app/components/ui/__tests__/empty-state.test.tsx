import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { EmptyState, NoResults } from '../empty-state';

const root = (container: HTMLElement) => container.querySelector<HTMLElement>('[data-empty-state]')!;
const iconBox = (container: HTMLElement) => container.querySelector<HTMLElement>('[data-empty-state-icon]');

describe('EmptyState (panel)', () => {
  it('is text-only by default', () => {
    const { container } = render(<EmptyState title="No members yet" description="Add people to this team." />);
    expect(iconBox(container)).toBeNull();
    expect(container.querySelector('svg')).toBeNull();
  });

  it('centres a column that fills the space, 200px tall, padded 40/24', () => {
    const { container } = render(<EmptyState title="No members yet" />);
    const el = root(container);
    expect(el.getAttribute('data-empty-state')).toBe('panel');
    expect(el.className).toContain('rt-r-fd-column');
    expect(el.className).toContain('rt-r-ai-center');
    expect(el.className).toContain('rt-r-jc-center');
    expect(el.className).toContain('rt-r-gap-1');
    expect(el.style.flex).toContain('1');
    expect(el.style.minHeight).toBe('200px');
    expect(el.style.padding).toBe('var(--space-7) var(--space-5)');
    expect(el.style.textAlign).toBe('center');
  });

  it('sets the title in size 2 medium on the primary text role', () => {
    render(<EmptyState title="No members yet" />);
    const title = screen.getByText('No members yet');
    expect(title.className).toContain('rt-r-size-2');
    expect(title.className).toContain('rt-r-weight-medium');
    expect(title.style.color).toBe('var(--text-primary)');
  });

  it('draws the title as a heading when asked, with the same type', () => {
    render(<EmptyState title="Q3 planning" titleAs="h2" />);
    const heading = screen.getByRole('heading', { level: 2, name: 'Q3 planning' });
    expect(heading.className).toContain('rt-r-size-2');
    expect(heading.className).toContain('rt-r-weight-medium');
    expect(heading.style.color).toBe('var(--text-primary)');
    expect(heading.style.margin).toBe('0px');
  });

  it('is not a heading by default', () => {
    render(<EmptyState title="No members yet" />);
    expect(screen.queryByRole('heading')).toBeNull();
  });

  it('sets the description on the secondary text role, 300px wide at most', () => {
    render(<EmptyState title="No members yet" description="Add people to this team." />);
    const description = screen.getByText('Add people to this team.');
    expect(description.className).toContain('rt-r-size-2');
    expect(description.style.color).toBe('var(--text-secondary)');
    expect(description.style.maxWidth).toBe('300px');
    expect(description.style.lineHeight).toBe('var(--line-height-2)');
  });

  it('shows an icon only when passed, hidden from assistive technology', () => {
    const { container } = render(<EmptyState title="Nothing here" icon={<svg data-testid="glyph" />} />);
    const box = iconBox(container)!;
    expect(box).not.toBeNull();
    expect(box.getAttribute('aria-hidden')).toBe('true');
    expect(box.style.marginBottom).toBe('var(--space-2)');
    expect(box.querySelector('[data-testid="glyph"]')).not.toBeNull();
  });

  it('puts the action under the text', () => {
    render(<EmptyState title="No members yet" action={<button type="button">Add member</button>} />);
    const wrapper = screen.getByRole('button', { name: 'Add member' }).parentElement!;
    expect(wrapper.style.marginTop).toBe('var(--space-3)');
  });
});

describe('NoResults', () => {
  it('shows the 40px empty glyph on gray 8 by default, hidden from assistive technology', () => {
    const { container } = render(<NoResults title="No users found" description="Try a different search." />);
    const box = iconBox(container)!;
    expect(box.getAttribute('aria-hidden')).toBe('true');
    expect(box.style.marginBottom).toBe('var(--space-3)');
    const svg = box.querySelector('svg')!;
    expect(svg.getAttribute('width')).toBe('40');
    expect(svg.getAttribute('height')).toBe('40');
    expect(svg.style.color).toBe('var(--gray-8)');
  });

  it('drops the glyph when icon is null', () => {
    const { container } = render(<NoResults title="No users found" description="Try a different search." icon={null} />);
    expect(iconBox(container)).toBeNull();
  });

  it('is 320px tall, padded 24, with a medium title and secondary description', () => {
    const { container } = render(<NoResults title="No users found" description="Try a different search." />);
    const el = root(container);
    expect(el.getAttribute('data-empty-state')).toBe('no-results');
    expect(el.style.minHeight).toBe('calc(var(--space-9) * 5)');
    expect(el.style.padding).toBe('var(--space-5)');
    const title = screen.getByText('No users found');
    expect(title.className).toContain('rt-r-weight-medium');
    expect(screen.getByText('Try a different search.').style.color).toBe('var(--text-secondary)');
  });

  it('takes no action', () => {
    // @ts-expect-error NoResults has no action slot.
    render(<NoResults title="No users found" description="Try a different search." action={<button type="button">x</button>} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('requires a description', () => {
    // @ts-expect-error description is required.
    const el = <NoResults title="No users found" />;
    expect(el).toBeTruthy();
  });
});

describe('EmptyState variants', () => {
  it('refuses an action on the no-results variant, and drops one passed anyway', () => {
    render(
      // @ts-expect-error no-results takes no action.
      <EmptyState variant="no-results" title="No users found" description="Try a different search." action={<button type="button">x</button>} />,
    );
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('requires a description on the no-results variant', () => {
    // @ts-expect-error description is required for no-results.
    const el = <EmptyState variant="no-results" title="No users found" />;
    expect(el).toBeTruthy();
  });

  it('still takes an action on the panel variant', () => {
    render(<EmptyState variant="panel" title="No members yet" action={<button type="button">Add member</button>} />);
    expect(screen.getByRole('button', { name: 'Add member' })).toBeTruthy();
  });
});

describe('copy', () => {
  it('holds no user-visible string of its own', () => {
    const src = readFileSync(path.join(__dirname, '../empty-state.tsx'), 'utf8');
    expect(src).not.toMatch(/useTranslation|\bt\(/);
    const { container } = render(<EmptyState title="T" description="D" />);
    expect(container.textContent).toBe('TD');
  });
});
