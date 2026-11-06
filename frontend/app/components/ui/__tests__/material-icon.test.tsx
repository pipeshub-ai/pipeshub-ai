import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import { MaterialIcon, DECORATIVE_BY_DEFAULT } from '../MaterialIcon';

afterEach(cleanup);

const span = (container: HTMLElement) => container.querySelector('span.material-icons-outlined') as HTMLElement;

describe('MaterialIcon', () => {
  it('draws the glyph at the size of its box', () => {
    const { container } = render(<MaterialIcon name="folder" size={16} />);
    const el = span(container);
    expect(el.style.fontSize).toBe('16px');
    expect(el.style.width).toBe('16px');
    expect(el.style.height).toBe('16px');
    expect(el.style.lineHeight).toBe('1');
  });

  it('uses weight 400 and an optical size from the drawn size, within 20 to 48', () => {
    const at = (size: number) => span(render(<MaterialIcon name="add" size={size} />).container).style.fontVariationSettings;
    expect(at(16)).toBe("'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20");
    cleanup();
    expect(at(24)).toBe("'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24");
    cleanup();
    expect(at(64)).toBe("'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 48");
  });

  it('draws the filled variant from the same font with the fill axis on', () => {
    const { container } = render(<MaterialIcon name="star" size={16} variant="filled" />);
    const el = span(container);
    expect(el.className).toBe('material-icons-outlined');
    expect(el.style.fontVariationSettings).toContain("'FILL' 1");
  });

  it('with a label, is an image with that name', () => {
    render(<MaterialIcon name="volume_up" label="Read aloud" />);
    const img = screen.getByRole('img', { name: 'Read aloud' });
    expect(img.getAttribute('aria-hidden')).toBeNull();
  });

  it('without a label, keeps today\'s behaviour until the decorative default flips', () => {
    const { container } = render(<MaterialIcon name="folder" />);
    const el = span(container);
    expect(el.getAttribute('role')).toBeNull();
    expect(el.getAttribute('aria-label')).toBeNull();
    expect(el.getAttribute('aria-hidden')).toBe(DECORATIVE_BY_DEFAULT ? 'true' : null);
  });
});
