import { describe, it, expect } from 'vitest';
import {
  getPersonalConnectorRedirectType,
  personalConnectorHref,
} from '../admin-access-helpers';
import type { Connector } from '../../types';

const asConnector = (partial: Record<string, unknown>): Connector => partial as unknown as Connector;

describe('personalConnectorHref', () => {
  it('encodes the connector type into the personal connectors route', () => {
    expect(personalConnectorHref('Notion Personal')).toBe(
      '/workspace/connectors/personal/?connectorType=Notion%20Personal'
    );
  });

  it('falls back to the unfiltered page when no type is known', () => {
    expect(personalConnectorHref(undefined)).toBe('/workspace/connectors/personal/');
  });
});

describe('getPersonalConnectorRedirectType', () => {
  it('reads the personal alternative declared by the connector', () => {
    expect(
      getPersonalConnectorRedirectType(asConnector({ personalConnectorType: 'Notion Personal' }))
    ).toBe('Notion Personal');
  });

  it('returns undefined when absent, so no link renders', () => {
    expect(getPersonalConnectorRedirectType(asConnector({}))).toBeUndefined();
    expect(getPersonalConnectorRedirectType(asConnector({ personalConnectorType: null }))).toBeUndefined();
    expect(getPersonalConnectorRedirectType(asConnector({ personalConnectorType: '' }))).toBeUndefined();
    expect(getPersonalConnectorRedirectType(null)).toBeUndefined();
  });
});
