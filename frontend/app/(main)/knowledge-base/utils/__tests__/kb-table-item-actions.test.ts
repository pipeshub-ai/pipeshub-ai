import { describe, expect, it } from 'vitest';
import { canDeleteKbTableItem, type KbTableItem } from '../kb-table-item-actions';

const hub = (nodeType: string, permission?: { canDelete?: boolean; role?: string }) =>
  ({ id: 'n1', name: 'n1', nodeType, origin: 'COLLECTION', permission }) as unknown as KbTableItem;

describe('canDeleteKbTableItem', () => {
  it('follows the row’s own permission, not its folder’s', () => {
    expect(canDeleteKbTableItem(hub('record', { canDelete: true, role: 'WRITER' }))).toBe(true);
    expect(canDeleteKbTableItem(hub('record', { canDelete: false, role: 'READER' }))).toBe(false);
    expect(canDeleteKbTableItem(hub('record'))).toBe(false);
  });

  it('lets only the owner delete a whole collection', () => {
    expect(canDeleteKbTableItem(hub('app', { canDelete: true, role: 'OWNER' }))).toBe(true);
    expect(canDeleteKbTableItem(hub('app', { canDelete: true, role: 'WRITER' }))).toBe(false);
  });
});
