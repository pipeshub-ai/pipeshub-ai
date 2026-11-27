import { describe, expect, it } from 'vitest';
import * as real from '../data-collection';
import * as stub from '../data-collection.stub';

describe('data-collection stub', () => {
  it('exports what the page takes from the real file', () => {
    // MetricsApi is used inside the real file only.
    const used = Object.keys(real).filter((name) => name !== 'MetricsApi').sort();
    expect(Object.keys(stub).sort()).toEqual(used);
  });

  it('shows nothing, never keeps the page waiting, and saves nothing', async () => {
    expect(stub.DataCollectionSection()).toBeNull();
    const setting = stub.useDataCollection();
    expect(setting).toMatchObject({ loaded: true, value: false, error: undefined });
    expect(Object.keys(setting).sort()).toEqual(['error', 'loaded', 'reread', 'save', 'validating', 'value']);
    await expect(setting.save(true, false)).resolves.toBeUndefined();
  });
});
