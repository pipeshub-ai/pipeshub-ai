import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { UserAvatar } from '../user-avatar';

describe('UserAvatar', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('gives the photo an empty alt, since the name is shown beside it', () => {
    // jsdom never loads images: report this one as loaded, so the photo is drawn instead of the initials.
    vi.spyOn(HTMLImageElement.prototype, 'complete', 'get').mockReturnValue(true);
    vi.spyOn(HTMLImageElement.prototype, 'naturalWidth', 'get').mockReturnValue(1);
    const { container } = render(<UserAvatar fullName="Maya Chen" src="/photo.png" />);
    expect(container.querySelector('img')?.getAttribute('alt')).toBe('');
  });
});
