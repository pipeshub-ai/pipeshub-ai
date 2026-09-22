import { describe, expect, it } from 'vitest';
import { attachmentErrorMessage } from '../attachment-error';

describe('attachmentErrorMessage', () => {
  it('shows a server reason that already names the file as-is', () => {
    const err = new Error("Couldn't read report.docx. It may be damaged or password-protected.");
    expect(attachmentErrorMessage('report.docx', err)).toBe(
      "Couldn't read report.docx. It may be damaged or password-protected.",
    );
  });

  it('names the file once in front of a reason that does not', () => {
    const err = { message: 'Network error. Please check your connection.' };
    expect(attachmentErrorMessage('notes.pdf', err)).toBe(
      "Couldn't attach notes.pdf. Network error. Please check your connection.",
    );
  });

  it('names a short file even when its name appears inside the reason', () => {
    const err = new Error('Upload of a.png failed on the server. Please try again.');
    expect(attachmentErrorMessage('a.png', err)).toBe(
      "Couldn't attach a.png. Upload of a.png failed on the server. Please try again.",
    );
  });

  it('shows the server\'s file-first messages as-is', () => {
    const err = new Error('a.png is empty. Attach a file that has content.');
    expect(attachmentErrorMessage('a.png', err)).toBe('a.png is empty. Attach a file that has content.');
  });

  it('still says what to do when there is no reason at all', () => {
    expect(attachmentErrorMessage('a.png', undefined)).toBe("Couldn't attach a.png. Please try again.");
  });
});
