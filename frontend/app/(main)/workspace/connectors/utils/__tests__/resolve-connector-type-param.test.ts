import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  normalizeConnectorTypeKey,
  resolveConnectorTypeParam,
} from '../resolve-connector-type-param.ts';

describe('normalizeConnectorTypeKey', () => {
  it('lowercases and strips spaces, hyphens, underscores', () => {
    assert.equal(normalizeConnectorTypeKey('OneDrive'), 'onedrive');
    assert.equal(normalizeConnectorTypeKey('ONEDRIVE'), 'onedrive');
    assert.equal(normalizeConnectorTypeKey('SharePoint Online'), 'sharepointonline');
    assert.equal(normalizeConnectorTypeKey('local-fs'), 'localfs');
  });
});

describe('resolveConnectorTypeParam', () => {
  const registry = [{ type: 'OneDrive' }, { type: 'SharePoint Online' }, { type: 'S3' }];

  it('returns null for empty param', () => {
    assert.equal(resolveConnectorTypeParam(null, registry, []), null);
    assert.equal(resolveConnectorTypeParam('  ', registry, []), null);
  });

  it('resolves case-insensitive enum-style params to canonical registry type', () => {
    assert.equal(resolveConnectorTypeParam('ONEDRIVE', registry, []), 'OneDrive');
    assert.equal(resolveConnectorTypeParam('onedrive', registry, []), 'OneDrive');
    assert.equal(resolveConnectorTypeParam('OneDrive', registry, []), 'OneDrive');
  });

  it('resolves spaced names case-insensitively', () => {
    assert.equal(
      resolveConnectorTypeParam('sharepoint online', registry, []),
      'SharePoint Online',
    );
  });

  it('falls back to active connectors when not in registry', () => {
    const active = [{ type: 'Web' }];
    assert.equal(resolveConnectorTypeParam('WEB', [], active), 'Web');
  });

  it('returns null when no connector matches', () => {
    assert.equal(resolveConnectorTypeParam('UnknownConnector', registry, []), null);
  });
});
