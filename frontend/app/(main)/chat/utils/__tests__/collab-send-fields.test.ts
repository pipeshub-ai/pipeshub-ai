import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  collabSendFields,
  newClientMessageId,
  pickCollabSendFields,
} from '../collab-send-fields';

const base = {
  active: true,
  hasAttachments: false,
  isAgent: false,
  shareToolResults: false,
};

afterEach(() => vi.unstubAllGlobals());

describe('collabSendFields', () => {
  it('sends nothing when the chat is solo or the flag is off', () => {
    expect(collabSendFields({ ...base, active: false, hasAttachments: true, isAgent: true, shareToolResults: true })).toEqual({});
  });

  it('carries the file consent', () => {
    expect(collabSendFields(base)).toEqual({ filesShared: false });
    expect(collabSendFields({ ...base, hasAttachments: true })).toMatchObject({ filesShared: true });
  });

  it('sends shareToolResults only in agent chats', () => {
    expect(collabSendFields({ ...base, shareToolResults: true })).not.toHaveProperty('shareToolResults');
    expect(collabSendFields({ ...base, isAgent: true })).toMatchObject({ shareToolResults: false });
    expect(collabSendFields({ ...base, isAgent: true, shareToolResults: true })).toMatchObject({ shareToolResults: true });
  });
});

describe('newClientMessageId', () => {
  it('uses randomUUID when available', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'uuid-1' });
    expect(newClientMessageId()).toBe('uuid-1');
  });

  it('falls back to a 1-64 character id on plain HTTP', () => {
    vi.stubGlobal('crypto', {});
    const id = newClientMessageId();
    expect(id.length).toBeGreaterThan(0);
    expect(id.length).toBeLessThanOrEqual(64);
    expect(newClientMessageId()).not.toBe(id);
  });
});

describe('pickCollabSendFields', () => {
  it('copies only the fields that are set', () => {
    expect(pickCollabSendFields({})).toEqual({});
    expect(
      pickCollabSendFields({
        clientMessageId: 'c',
        baseSeq: 0,
        filesShared: false,
        shareToolResults: true,
        resume: { toolCallMessageId: 't1' },
      }),
    ).toEqual({
      clientMessageId: 'c',
      baseSeq: 0,
      filesShared: false,
      shareToolResults: true,
      resume: { toolCallMessageId: 't1' },
    });
  });
});
