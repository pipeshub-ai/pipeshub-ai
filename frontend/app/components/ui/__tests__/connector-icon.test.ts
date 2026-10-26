import { describe, expect, it } from 'vitest';
import { resolveConnectorType } from '../ConnectorIcon';

describe('resolveConnectorType', () => {
  it('gives API action groups the API icon', () => {
    expect(resolveConnectorType('api_action:0123456789abcdef0123456789abcdef')).toBe('api');
    expect(resolveConnectorType('api')).toBe('api');
  });

  it('leaves app slugs to their own icons', () => {
    expect(resolveConnectorType('jira')).toBe('jira');
  });

  it('maps the bare Google toolset slugs to their logos', () => {
    expect(resolveConnectorType('docs')).toBe('google-docs');
    expect(resolveConnectorType('sheets')).toBe('google-sheets');
    expect(resolveConnectorType('Docs')).toBe('google-docs');
    expect(resolveConnectorType('drive')).toBe('google-drive');
  });
});
