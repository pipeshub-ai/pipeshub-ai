/**
 * R37-SECB SEC-B-04: the approval card shows a held call's
 * arguments as the model wrote them, so text an injection chose can make a dangerous call read as a safe one.
 * It failed (`it.fails`) until R40-TEXT made every string the card shows display-safe (ADR-TEXT-01).
 */
import { describe, expect, it } from 'vitest';
import { approvalAction, previewPayload } from '../../../approval-preview';

const BIDI = /[‎‏‪-‮⁦-⁩]/;
const INVISIBLE = /[​-‍⁠﻿]/;

describe('SEC-B-04: what the approval card shows cannot be steered', () => {
  it('a recipient with a right-to-left override is shown without it', () => {
    // Renders as "bob@live.com"-looking text while the address is something else.
    const [field] = previewPayload(JSON.stringify({ to: '‮moc.live@bob‬' }));
    expect(field.value).not.toMatch(BIDI);
  });

  it('zero-width characters in a recipient are made visible or removed', () => {
    const [field] = previewPayload(JSON.stringify({ to: 'ceo@internal​.example' }));
    expect(field.value).not.toMatch(INVISIBLE);
  });

  it("a tool's display name cannot hide its bidi-reordered action", () => {
    expect(approvalAction('mcp__x', '‮atad lla eteled‬ Read calendar').action).not.toMatch(BIDI);
  });
});
