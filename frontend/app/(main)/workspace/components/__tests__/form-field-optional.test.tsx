import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

import { FormField } from '../form-field';

afterEach(() => cleanup());

const labelText = (label: string) =>
  render(
    <Theme>
      <FormField label={label} optional>
        <input />
      </FormField>
    </Theme>,
  ).container.querySelector('label')?.textContent;

describe('FormField optional suffix', () => {
  it('adds "(optional)" to a plain label', () => {
    expect(labelText('Base URL')).toBe('Base URL(optional)');
  });

  it('does not repeat it when the label already ends with it', () => {
    expect(labelText('Model Friendly Name (Optional)')).toBe('Model Friendly Name (Optional)');
    expect(labelText('Base URL (optional)')).toBe('Base URL (optional)');
  });
});
