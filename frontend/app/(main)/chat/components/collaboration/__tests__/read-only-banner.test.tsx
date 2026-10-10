import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { ReadOnlyBanner } from '../read-only-banner';

afterEach(cleanup);
const show = (ui: React.ReactElement) => render(<Theme>{ui}</Theme>);

describe('ReadOnlyBanner accessLost', () => {
  it('keeps the "what you see is kept" text while messages are shown', () => {
    show(<ReadOnlyBanner reason="accessLost" hasMessages />);
    expect(screen.getByTestId('read-only-banner').textContent).toContain('What you see here is kept');
  });

  it('defaults to the kept-text when hasMessages is omitted', () => {
    show(<ReadOnlyBanner reason="accessLost" />);
    expect(screen.getByTestId('read-only-banner').textContent).toContain('What you see here is kept');
  });

  it('uses the neutral text when nothing is shown, without saying which cause', () => {
    show(<ReadOnlyBanner reason="accessLost" hasMessages={false} />);
    const text = screen.getByTestId('read-only-banner').textContent ?? '';
    expect(text).toContain("This chat isn't available to you. It may have been deleted or your access removed.");
    expect(text).not.toContain('kept');
  });

  it('leaves the other reasons alone', () => {
    show(<ReadOnlyBanner reason="readOnly" hasMessages={false} />);
    expect(screen.getByTestId('read-only-banner').textContent).not.toContain("isn't available");
  });
});
