import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { ArtifactsPanel } from '../artifacts-panel';
import { buildChatArtifact, unavailableArtifact } from '../../../utils/build-chat-artifact';

afterEach(() => cleanup());

describe('ArtifactsPanel: a file a shared viewer may not open (SS0, AF-2)', () => {
  it('renders a neutral placeholder with no name, preview or download', () => {
    const open = buildChatArtifact({ id: 'a1', fileName: 'open.csv', mimeType: 'text/csv', recordId: 'a1' });
    render(
      <Theme>
        <ArtifactsPanel artifacts={[open, unavailableArtifact('hidden-1')]} />
      </Theme>,
    );
    const placeholders = screen.getAllByTestId('artifact-unavailable-card');
    expect(placeholders).toHaveLength(1);
    expect(placeholders[0]!.textContent).toBe(`lock${en.chat.artifactUnavailable}`);
    expect(placeholders[0]!.querySelector('button, a')).toBeNull();
    expect(screen.getByText('open.csv')).toBeTruthy();
  });
});
