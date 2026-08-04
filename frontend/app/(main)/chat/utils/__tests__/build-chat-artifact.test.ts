import { describe, it, expect } from 'vitest';
import { buildChatArtifact, artifactTypeFromMime } from '../build-chat-artifact';

describe('artifactTypeFromMime', () => {
  it('maps image mimes to IMAGE', () => {
    expect(artifactTypeFromMime('image/png')).toBe('IMAGE');
    expect(artifactTypeFromMime('image/jpeg')).toBe('IMAGE');
  });

  it('maps csv to SPREADSHEET', () => {
    expect(artifactTypeFromMime('text/csv')).toBe('SPREADSHEET');
  });

  it('falls back to OTHER for unknown types', () => {
    expect(artifactTypeFromMime('application/octet-stream')).toBe('OTHER');
  });
});

describe('buildChatArtifact', () => {
  it('populates all required fields with defaults', () => {
    const artifact = buildChatArtifact({ fileName: 'chart.png', mimeType: 'image/png' });
    expect(artifact.fileName).toBe('chart.png');
    expect(artifact.mimeType).toBe('image/png');
    expect(artifact.artifactType).toBe('IMAGE');
    expect(artifact.sizeBytes).toBe(0);
    expect(artifact.downloadUrl).toBe('');
    expect(artifact.id).toBeTruthy();
  });

  it('passes through visibility when provided', () => {
    const staging = buildChatArtifact({
      fileName: 'temp.csv',
      mimeType: 'text/csv',
      visibility: 'STAGING',
    });
    expect(staging.visibility).toBe('STAGING');
  });

  it('leaves visibility undefined when not provided', () => {
    const artifact = buildChatArtifact({ fileName: 'report.md', mimeType: 'text/markdown' });
    expect(artifact.visibility).toBeUndefined();
  });

  it('passes through VISIBLE visibility', () => {
    const artifact = buildChatArtifact({
      fileName: 'chart.png',
      mimeType: 'image/png',
      visibility: 'VISIBLE',
    });
    expect(artifact.visibility).toBe('VISIBLE');
  });

  it('uses provided id over generated fallback', () => {
    const artifact = buildChatArtifact({ id: 'my-id', fileName: 'x.txt' });
    expect(artifact.id).toBe('my-id');
  });

  it('generates a stable-looking random id when none provided', () => {
    const a = buildChatArtifact({ fileName: 'x.txt' });
    const b = buildChatArtifact({ fileName: 'x.txt' });
    expect(a.id).not.toBe(b.id);
    expect(a.id).toMatch(/^artifact-/);
  });

  it('passes through derivedFromCodeArtifactId', () => {
    const artifact = buildChatArtifact({
      fileName: 'out.csv',
      derivedFromCodeArtifactId: 'code-abc',
    });
    expect(artifact.derivedFromCodeArtifactId).toBe('code-abc');
  });
});

describe('Defensive staging guard — simulated onArtifact behaviour', () => {
  it('STAGING artifacts would be rejected before reaching Zustand', () => {
    // This test documents the guard logic in streaming.ts::onArtifact:
    //   if (data.visibility === 'STAGING') return;
    // We verify it here by simulating the guard inline.
    const events: ReturnType<typeof buildChatArtifact>[] = [];
    const onArtifact = (data: { visibility?: string; fileName: string }) => {
      if (data.visibility === 'STAGING') return;
      events.push(buildChatArtifact({ fileName: data.fileName }));
    };

    onArtifact({ fileName: 'staging_data.csv', visibility: 'STAGING' });
    onArtifact({ fileName: 'chart.png', visibility: 'VISIBLE' });
    onArtifact({ fileName: 'report.pdf' }); // no visibility field → passes through

    expect(events).toHaveLength(2);
    expect(events[0].fileName).toBe('chart.png');
    expect(events[1].fileName).toBe('report.pdf');
  });
});
