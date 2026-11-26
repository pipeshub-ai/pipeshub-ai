import { describe, expect, it } from 'vitest';
import { effectiveMimeType } from '@/lib/utils/file-icon-utils';
import { buildChatArtifact } from '@/app/(main)/chat/utils/build-chat-artifact';
import { resolvePreviewIconExtension } from '@/app/components/file-preview/utils';

const PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';

describe('QAFIX4 B3: a file stored as octet-stream is typed by its name', () => {
  it.each([
    ['deck.pptx', PPTX],
    ['notes.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    ['sheet.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    ['report.pdf', 'application/pdf'],
    ['chart.PNG', 'image/png'],
    ['data.csv', 'text/csv'],
    ['readme.md', 'text/markdown'],
  ])('%s', (name, mime) => {
    expect(effectiveMimeType(name, 'application/octet-stream')).toBe(mime);
    expect(effectiveMimeType(name, '')).toBe(mime);
    expect(effectiveMimeType(name, undefined)).toBe(mime);
  });

  it('keeps a real mime and leaves an unknown file as octet-stream', () => {
    expect(effectiveMimeType('x.pptx', 'image/png')).toBe('image/png');
    expect(effectiveMimeType('blob.bin', 'application/octet-stream')).toBe('application/octet-stream');
  });

  it('a pptx card stored as OTHER / octet-stream reads as a presentation', () => {
    const artifact = buildChatArtifact({ fileName: 'deck.pptx', mimeType: 'application/octet-stream', artifactType: 'OTHER' });
    expect(artifact.mimeType).toBe(PPTX);
    expect(artifact.artifactType).toBe('PRESENTATION');
  });

  it('an explicit type other than OTHER is kept', () => {
    expect(buildChatArtifact({ fileName: 'x.pptx', mimeType: PPTX, artifactType: 'DOCUMENT' }).artifactType).toBe('DOCUMENT');
  });

  it('the preview header badge follows the source file, not its PDF conversion', () => {
    expect(resolvePreviewIconExtension(null, 'application/pdf', PPTX)).toBe('ppt');
    expect(resolvePreviewIconExtension(null, 'application/pdf')).toBe('pdf');
  });
});
