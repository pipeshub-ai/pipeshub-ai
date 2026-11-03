import { describe, expect, it } from 'vitest';
import { getInitials } from '../user-avatar';

describe('getInitials', () => {
  it('uses the email local part when no name is set', () => {
    expect(getInitials({ email: 'sofia.rossi@example.com' })).toBe('SR');
  });

  it('treats an email passed as the name like an email, never using ".com"', () => {
    expect(getInitials({ fullName: 'sofia.rossi@example.com' })).toBe('SR');
    expect(getInitials({ fullName: 'ethan@example.com' })).toBe('ET');
  });

  it('splits the email local part on any non-letter', () => {
    expect(getInitials({ email: 'ethan+brown2@example.com' })).toBe('EB');
  });

  it('keeps real names as they are', () => {
    expect(getInitials({ fullName: 'Acme Corporation' })).toBe('AC');
  });
});
