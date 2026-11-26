import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Select, Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

import { FormField, FormFieldSelectTrigger, FormFieldTextArea, FormFieldTextField } from '../form-field';
import { SelectDropdown } from '../select-dropdown';
import { TagInput } from '../tag-input';

afterEach(() => cleanup());

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const field = (control: React.ReactNode | ((ids: { id: string }) => React.ReactNode)) =>
  render(
    <Theme>
      <FormField label="Team name" description="Shown to members">
        {control}
      </FormField>
    </Theme>,
  );

describe('FormField names its control', () => {
  it.each([
    ['FormFieldTextField', <FormFieldTextField key="t" />],
    ['FormFieldTextArea', <FormFieldTextArea key="a" />],
    ['a render-prop native input', (ids: { id: string }) => <input {...ids} />],
    ['TagInput', <TagInput key="g" tags={[]} onTagsChange={() => {}} />],
  ])('FFCTRL-1: %s is named by the label and described by the help text', (_, control) => {
    field(control);
    const box = screen.getByLabelText('Team name');
    const help = (box.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent);
    expect(help).toEqual(['Shown to members']);
  });

  it('FFCTRL-2: a Select trigger and the SelectDropdown button are named by the label', () => {
    field(
      <Select.Root>
        <FormFieldSelectTrigger placeholder="Pick" />
        <Select.Content>
          <Select.Item value="a">A</Select.Item>
        </Select.Content>
      </Select.Root>,
    );
    expect(screen.getByRole('combobox', { name: 'Team name' })).toBeTruthy();
    cleanup();
    field(<SelectDropdown value={null} onChange={() => {}} options={[{ value: 'a', label: 'A' }]} />);
    expect(screen.getByRole('button', { name: 'Team name' })).toBeTruthy();
  });
});
