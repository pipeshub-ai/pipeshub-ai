import { describe, expect, it } from 'vitest';
import { resolveConnectorType } from '../ConnectorIcon';

describe('resolveConnectorType', () => {
  it('gives API action groups the API icon', () => {
    expect(resolveConnectorType('api_action:0123456789abcdef0123456789abcdef')).toBe('api');
    expect(resolveConnectorType('api')).toBe('api');
  });

  it('leaves app slugs to their own icons', () => {
    expect(resolveConnectorType('jira')).toBe('jira');
    expect(resolveConnectorType('docusign')).toBe('docusign');
    expect(resolveConnectorType('okta')).toBe('okta');
    expect(resolveConnectorType('zoom')).toBe('zoom');
    expect(resolveConnectorType('paypal')).toBe('paypal');
    expect(resolveConnectorType('PayPal')).toBe('paypal');
    expect(resolveConnectorType('gitlab')).toBe('gitlab');
    expect(resolveConnectorType('GitLab')).toBe('gitlab');
    expect(resolveConnectorType('egnyte')).toBe('egnyte');
    expect(resolveConnectorType('intercom')).toBe('intercom');
    expect(resolveConnectorType('granola')).toBe('granola');
    expect(resolveConnectorType('fireflies')).toBe('fireflies');
    expect(resolveConnectorType('fathom')).toBe('fathom');
    expect(resolveConnectorType('instantly')).toBe('instantly');
    expect(resolveConnectorType('Instantly')).toBe('instantly');
    expect(resolveConnectorType('mixpanel')).toBe('mixpanel');
    expect(resolveConnectorType('Mixpanel')).toBe('mixpanel');
  });

  it('matches Intercom, Granola, Fireflies, Fathom and Instantly as whole words only', () => {
    expect(resolveConnectorType('Fathom MCP')).toBe('fathom');
    expect(resolveConnectorType('Instantly MCP')).toBe('instantly');
    expect(resolveConnectorType('my-fireflies-server')).toBe('fireflies');
    expect(resolveConnectorType('my-instantly-server')).toBe('instantly');
    expect(resolveConnectorType('Unfathomable Drive')).toBe('google-drive');
    expect(resolveConnectorType('Intercomm')).toBe('generic');
  });

  it('maps the bare Google toolset slugs to their logos', () => {
    expect(resolveConnectorType('docs')).toBe('google-docs');
    expect(resolveConnectorType('sheets')).toBe('google-sheets');
    expect(resolveConnectorType('Docs')).toBe('google-docs');
    expect(resolveConnectorType('drive')).toBe('google-drive');
  });

  it('maps Vercel MCP servers to the Vercel logo', () => {
    expect(resolveConnectorType('vercel')).toBe('vercel');
    expect(resolveConnectorType('Vercel MCP')).toBe('vercel');
  });
});
