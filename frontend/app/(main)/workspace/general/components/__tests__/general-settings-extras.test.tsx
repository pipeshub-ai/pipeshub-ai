/** The general page takes more settings cards from `GeneralSettingsExtras` (config.ts); this build's default adds none. */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { GeneralSettingsExtras } from '@/config';
import { GeneralSettingsExtras as DefaultExtras } from '../general-settings-extras';

describe('GeneralSettingsExtras', () => {
  it('is the default this build exports, and renders nothing', () => {
    expect(GeneralSettingsExtras).toBe(DefaultExtras);
    expect(render(<GeneralSettingsExtras />).container.innerHTML).toBe('');
  });
});
