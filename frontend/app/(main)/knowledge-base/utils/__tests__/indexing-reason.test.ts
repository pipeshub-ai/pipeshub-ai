/**
 * A stored failure reason can speak to the person who added the file. Anyone else reads the line named for it: told
 * "you", another reader would take it for themselves. None is named by default.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import type { TFunction } from 'i18next';
import { indexingReasonFor, setReasonsSpokenToUploader } from '../indexing-reason';

const t = ((key: string) => `t(${key})`) as unknown as TFunction;

const TO_YOU = 'You have no access to the folder this came from, so this file was not indexed. Ask an admin for access, then reindex.';
const ANYONE = 'Unsupported file type';

afterEach(() => setReasonsSpokenToUploader(undefined));

describe('indexingReasonFor, with no reason named', () => {
  it('leaves every reason as stored, for every reader', () => {
    expect(indexingReasonFor(TO_YOU, t)).toBe(TO_YOU);
    expect(indexingReasonFor(TO_YOU, t, true)).toBe(TO_YOU);
    expect(indexingReasonFor(ANYONE, t)).toBe(ANYONE);
    expect(indexingReasonFor(undefined, t)).toBeUndefined();
    expect(indexingReasonFor(null, t)).toBeNull();
  });
});

describe('indexingReasonFor, with a reason that speaks to the uploader', () => {
  beforeEach(() => setReasonsSpokenToUploader(new Map([[TO_YOU, 'kb.reasonForOthers']])));

  it('tells other readers the line named for it, not the words meant for the uploader', () => {
    expect(indexingReasonFor(TO_YOU, t)).toBe('t(kb.reasonForOthers)');
  });

  it('keeps the words written for the uploader, for the uploader', () => {
    expect(indexingReasonFor(TO_YOU, t, true)).toBe(TO_YOU);
  });

  it('leaves every other reason as stored: they are true for any reader', () => {
    expect(indexingReasonFor(ANYONE, t)).toBe(ANYONE);
    expect(indexingReasonFor('constructor', t)).toBe('constructor');
    expect(indexingReasonFor(undefined, t)).toBeUndefined();
    expect(indexingReasonFor(null, t)).toBeNull();
  });

  it('matches a reason with space around it', () => {
    expect(indexingReasonFor(`  ${TO_YOU}\n`, t)).toBe('t(kb.reasonForOthers)');
  });

  it('names none again once the default is restored', () => {
    setReasonsSpokenToUploader(undefined);
    expect(indexingReasonFor(TO_YOU, t)).toBe(TO_YOU);
  });
});
