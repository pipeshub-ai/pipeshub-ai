import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach, beforeAll, afterAll } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

// Icons render their ligature name as text, which would leak into accessible names.
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SchemaFormField } from '../schema-form-field';
import type { AuthSchemaField } from '../../types';
import { installDomShims, renderInTheme, inputByLabel } from '../../__tests__/fixtures';

type ChangeFn = (name: string, value: unknown) => void;

function renderField(
  field: AuthSchemaField,
  props: { value?: unknown; error?: string; disabled?: boolean; visible?: boolean } = {}
) {
  const onChange = vi.fn<ChangeFn>();
  const view = renderInTheme(
    <SchemaFormField field={field} value={props.value} onChange={onChange} {...props} />
  );
  return { onChange, ...view };
}

/** Re-renders with the value the parent would pass back, like a controlled form does. */
function renderControlled(field: AuthSchemaField, initial: unknown = '') {
  const calls: unknown[] = [];
  function Harness() {
    const [value, setValue] = React.useState<unknown>(initial);
    return (
      <SchemaFormField
        field={field}
        value={value}
        onChange={(_name, next) => {
          calls.push(next);
          setValue(next);
        }}
      />
    );
  }
  renderInTheme(<Harness />);
  return calls;
}

const secretField: AuthSchemaField = {
  name: 'apiToken',
  displayName: 'API token',
  fieldType: 'PASSWORD',
  required: true,
  isSecret: true,
};

beforeEach(() => installDomShims());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SchemaFormField: secrets', () => {
  it('masks a password field and never prints the secret as page text', () => {
    renderField(secretField, { value: 'tok_live_123' });

    const input = inputByLabel('API token');
    expect(input.type).toBe('password');
    expect(input.value).toBe('tok_live_123');
    expect(screen.queryByText('tok_live_123')).toBeNull();
  });

  it('lets the user reveal and hide the secret with a named toggle', () => {
    renderField(secretField, { value: 'tok_live_123' });
    const input = inputByLabel('API token');

    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(input.type).toBe('text');

    fireEvent.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(input.type).toBe('password');
  });

  it('does not write the secret to the console while typing it', () => {
    const spies = (['log', 'info', 'debug', 'warn', 'error'] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {})
    );
    renderControlled(secretField);

    fireEvent.change(inputByLabel('API token'), { target: { value: 'tok_live_secret' } });
    fireEvent.blur(inputByLabel('API token'));

    const logged = spies.flatMap((s) => s.mock.calls).map((args) => JSON.stringify(args));
    expect(logged.some((line) => line.includes('tok_live_secret'))).toBe(false);
  });
});

describe('SchemaFormField: labels, required and errors', () => {
  it('marks a required field and reports its error beside the input', () => {
    renderField(
      { name: 'email', displayName: 'Account email', fieldType: 'EMAIL', required: true },
      { value: '', error: 'Account email is required' }
    );

    const input = inputByLabel('Account email');
    expect(input.type).toBe('email');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Account email is required')).toBeTruthy();
    expect(screen.getByText('*')).toBeTruthy();
    expect(screen.queryByText('(optional)')).toBeNull();
  });

  it('labels a field the schema marks as not required as optional', () => {
    renderField({ name: 'label', displayName: 'Label', fieldType: 'TEXT', required: false });

    expect(screen.getByText('(optional)')).toBeTruthy();
    expect(inputByLabel('Label').getAttribute('aria-invalid')).toBeNull();
  });

  it('reports each keystroke under the field name', () => {
    const { onChange } = renderField({
      name: 'email',
      displayName: 'Account email',
      fieldType: 'EMAIL',
      required: true,
    });

    fireEvent.change(inputByLabel('Account email'), { target: { value: 'ops@example.com' } });
    expect(onChange).toHaveBeenCalledWith('email', 'ops@example.com');
  });

  it('adds https:// to a bare host when the user leaves a URL field', () => {
    const calls = renderControlled({
      name: 'baseUrl',
      displayName: 'Site URL',
      fieldType: 'URL',
      required: true,
    });
    const input = inputByLabel('Site URL');

    fireEvent.change(input, { target: { value: '  acme.atlassian.net ' } });
    fireEvent.blur(input);

    expect(calls[calls.length - 1]).toBe('https://acme.atlassian.net');
    expect(input.value).toBe('https://acme.atlassian.net');
  });

  it('leaves a URL with its own scheme untouched on blur', () => {
    const calls = renderControlled(
      { name: 'baseUrl', displayName: 'Site URL', fieldType: 'URL', required: true },
      'http://intranet.local'
    );
    fireEvent.blur(inputByLabel('Site URL'));
    expect(calls).toEqual([]);
  });

  it('turns number input into a number, and an emptied box into an empty value', () => {
    const { onChange } = renderField(
      { name: 'port', displayName: 'Port', fieldType: 'NUMBER', required: true },
      { value: 8080 }
    );
    const input = inputByLabel('Port');

    fireEvent.change(input, { target: { value: '443' } });
    expect(onChange).toHaveBeenLastCalledWith('port', 443);

    fireEvent.change(input, { target: { value: '' } });
    expect(onChange).toHaveBeenLastCalledWith('port', '');
  });

  it('disables the input when the form is read-only', () => {
    renderField(secretField, { value: 'x', disabled: true });
    expect(inputByLabel('API token').disabled).toBe(true);
  });

  it('renders nothing when the field is hidden by a display rule', () => {
    const { container } = renderField(secretField, { visible: false });
    expect(container.querySelector('[data-ph-field]')).toBeNull();
  });

  it('toggles a checkbox field and shows its required marker', () => {
    const { onChange } = renderField(
      { name: 'acceptTerms', displayName: 'I own this account', fieldType: 'CHECKBOX', required: true },
      { value: false, error: 'I own this account must be true' }
    );

    expect(screen.getByText(/I own this account \*/)).toBeTruthy();
    expect(screen.getByText('I own this account must be true')).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onChange).toHaveBeenCalledWith('acceptTerms', true);
  });
});

describe('SchemaFormField: tags', () => {
  const tagsField: AuthSchemaField = {
    name: 'projects',
    displayName: 'Projects',
    fieldType: 'TAGS',
    required: false,
  };

  it('adds a tag on Enter and ignores a repeat that differs only by case', () => {
    const calls = renderControlled(tagsField, []);
    const input = screen.getByPlaceholderText('Type and press Enter to add tags');

    fireEvent.change(input, { target: { value: ' ENG ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.change(input, { target: { value: 'eng' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(calls).toEqual([['ENG']]);
    expect(screen.getByText('ENG')).toBeTruthy();
    expect((input as HTMLInputElement).value).toBe('eng');
  });

  it('removes a tag with its named remove button', () => {
    const calls = renderControlled(tagsField, ['ENG', 'OPS']);
    fireEvent.click(screen.getByRole('button', { name: 'Remove ENG' }));
    expect(calls[calls.length - 1]).toEqual(['OPS']);
    expect(screen.queryByText('ENG')).toBeNull();
  });
});

describe('SchemaFormField: file upload', () => {
  // jsdom's File has no `text()`; the component reads uploads with it.
  const hadText = 'text' in File.prototype;
  beforeAll(() => {
    if (hadText) return;
    Object.defineProperty(File.prototype, 'text', {
      configurable: true,
      value(this: File) {
        return new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = () => reject(reader.error);
          reader.readAsText(this);
        });
      },
    });
  });
  afterAll(() => {
    if (!hadText) delete (File.prototype as { text?: unknown }).text;
  });

  const keyFileField: AuthSchemaField = {
    name: 'serviceAccountKey',
    displayName: 'Service account key',
    fieldType: 'FILE',
    required: true,
    validation: {
      acceptedFileTypes: ['.json'],
      validationRules: [
        { type: 'json_valid', errorMessage: 'The key file must be valid JSON.' },
        {
          type: 'json_has_fields',
          requiredFields: ['client_email', 'private_key'],
          errorMessage: 'The key file is missing: {missing}',
        },
      ],
    },
  };

  function fileInput(container: HTMLElement): HTMLInputElement {
    return container.querySelector('input[type="file"]') as HTMLInputElement;
  }

  function jsonFile(body: string, name = 'key.json') {
    return new File([body], name, { type: 'application/json' });
  }

  it('shows the accepted types and size limit before a file is chosen', () => {
    renderField(keyFileField);
    expect(screen.getByText('Click to upload file')).toBeTruthy();
    expect(screen.getByText('.json')).toBeTruthy();
    expect(screen.getByText('Max 256 KB')).toBeTruthy();
  });

  it('refuses a file over the size limit and explains the limit', async () => {
    const { container, onChange } = renderField(keyFileField);
    const big = jsonFile('x'.repeat(256 * 1024 + 1));

    fireEvent.change(fileInput(container), { target: { files: [big] } });

    expect(
      await screen.findByText(/File is too large \(257 KB\)\. Maximum size is 256 KB/)
    ).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('reports the schema rule a file breaks, with the missing keys named', async () => {
    const { container, onChange } = renderField(keyFileField);

    fireEvent.change(fileInput(container), { target: { files: [jsonFile('not json')] } });
    expect(await screen.findByText('The key file must be valid JSON.')).toBeTruthy();

    fireEvent.change(fileInput(container), {
      target: { files: [jsonFile('{"client_email":"a@b.c"}')] },
    });
    expect(await screen.findByText('The key file is missing: private_key')).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('hands a valid file to the form and shows its name', async () => {
    const { container, onChange } = renderField(keyFileField);
    const body = '{"client_email":"a@b.c","private_key":"-----BEGIN-----"}';

    fireEvent.change(fileInput(container), { target: { files: [jsonFile(body, 'svc.json')] } });

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('serviceAccountKey', body));
    expect(await screen.findByText('svc.json')).toBeTruthy();
    expect(screen.queryByText(body)).toBeNull();
  });

  it('shows a saved file as configured without revealing it, and Replace clears it', () => {
    const { onChange } = renderField(keyFileField, { value: '{"private_key":"secret"}' });

    expect(screen.getByText('Already configured')).toBeTruthy();
    expect(screen.queryByText(/secret/)).toBeNull();

    fireEvent.click(screen.getByText('Replace'));
    expect(onChange).toHaveBeenCalledWith('serviceAccountKey', '');
  });
});
