import { describe, it, expect } from 'vitest';
import { hasUnpublishedChanges, nextVersionNumber, summarizeVersionDefinition } from '../versions';

describe('WP-21 OWD-14: version helpers', () => {
  it('OWD-14 H-1: summarizes a frozen definition in names and counts, never its prompts', () => {
    const summary = summarizeVersionDefinition({
      models: ['k1_gpt-4o', 'k2_claude_sonnet', 'k1_gpt-4o', { modelFriendlyName: 'Llama' }, 7],
      toolsets: [{ name: 'jira', displayName: 'Jira' }, { instanceName: 'Team Slack', name: 'slack' }, null],
      mcpServers: [{ displayName: 'GitHub' }],
      knowledge: [{}, {}],
      skills: [{}],
      instructions: '  Be brief.  ',
      systemPrompt: 'hidden',
    });
    expect(summary).toEqual({
      models: ['gpt-4o', 'claude_sonnet', 'Llama'],
      toolsets: ['Jira', 'Team Slack'],
      mcpServers: ['GitHub'],
      knowledgeCount: 2,
      skillsCount: 1,
      instructionsLength: 9,
    });
  });

  it('OWD-14 H-2: an empty or missing definition summarizes to nothing', () => {
    expect(summarizeVersionDefinition(undefined)).toEqual({
      models: [],
      toolsets: [],
      mcpServers: [],
      knowledgeCount: 0,
      skillsCount: 0,
      instructionsLength: 0,
    });
  });

  it('OWD-14 H-3: unpublished means saved-but-unpublished or edited-but-unsaved; the next number follows the latest', () => {
    expect(hasUnpublishedChanges(null, false)).toBe(false);
    expect(hasUnpublishedChanges(null, true)).toBe(true);
    expect(hasUnpublishedChanges({ latestVersion: 2, latestDigest: '', unpublishedChanges: true }, false)).toBe(true);
    expect(nextVersionNumber({ latestVersion: 4 })).toBe(5);
    expect(nextVersionNumber({ latestVersion: 0 })).toBeNull();
    expect(nextVersionNumber(undefined)).toBeNull();
  });
});
