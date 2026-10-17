/**
 * What each chat shows for an artifact after it was forked and then edited in
 * the fork. A card is rebuilt from the `::artifact[...]` marker stored in the
 * message text, and that marker pins a version. A fork copies the message text,
 * so its old cards carry the original's version; the edit adds a new marker to
 * the fork only.
 */
import { describe, it, expect } from 'vitest';
import { parseArtifactMarkers } from '../parse-download-markers';

const marker = (version: number) =>
  `::artifact[report.md](record:rec-1){text/markdown|doc-1|rec-1|OTHER|${version}}`;

/** The same rule `MessageList` uses for the "newer version available" hint. */
const highestVersions = (messages: string[]): Map<string, number> => {
  const versions = new Map<string, number>();
  for (const content of messages) {
    for (const artifact of parseArtifactMarkers(content).artifacts) {
      if (!artifact.recordId || artifact.version === undefined) continue;
      const current = versions.get(artifact.recordId);
      if (current === undefined || artifact.version > current) versions.set(artifact.recordId, artifact.version);
    }
  }
  return versions;
};

describe('an artifact edited in a fork', () => {
  const originalMessages = [`Here is the report.\n\n${marker(1)}`];
  const forkMessages = [`Here is the report.\n\n${marker(1)}`, `Updated it.\n\n${marker(2)}`];

  it('keeps showing version 1 in the original, with no newer-version hint', () => {
    const card = parseArtifactMarkers(originalMessages[0]!).artifacts[0]!;
    expect(card.recordId).toBe('rec-1');
    expect(card.version).toBe(1);
    expect(highestVersions(originalMessages).get('rec-1')).toBe(1);
  });

  it('shows the copied card as version 1 in the fork, with a hint that version 2 exists', () => {
    const copied = parseArtifactMarkers(forkMessages[0]!).artifacts[0]!;
    expect(copied.version).toBe(1);
    expect(highestVersions(forkMessages).get('rec-1')).toBe(2);
    expect(parseArtifactMarkers(forkMessages[1]!).artifacts[0]!.version).toBe(2);
  });
});
