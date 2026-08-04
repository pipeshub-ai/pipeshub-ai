import { describe, it, expect } from 'vitest';
import { isFileTypeSupported } from '../utils/attachment-file-types';

function makeFile(name: string, type: string): Pick<File, 'type' | 'name'> {
  return { name, type };
}

describe('isFileTypeSupported', () => {
  it('accepts DOCX / XLSX / CSV / TSV by MIME type, and by extension when MIME is empty (Windows)', () => {
    expect(
      isFileTypeSupported(
        makeFile(
          'report.docx',
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        ),
      ),
    ).toBe(true);
    expect(
      isFileTypeSupported(
        makeFile(
          'data.xlsx',
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        ),
      ),
    ).toBe(true);
    expect(isFileTypeSupported(makeFile('rows.csv', 'text/csv'))).toBe(true);
    expect(isFileTypeSupported(makeFile('rows.tsv', 'text/tab-separated-values'))).toBe(true);
    expect(isFileTypeSupported(makeFile('report.docx', ''))).toBe(true);
  });

  it('rejects unsupported legacy types (.doc / .xls / .ppt)', () => {
    expect(isFileTypeSupported(makeFile('legacy.doc', 'application/msword'))).toBe(false);
    expect(isFileTypeSupported(makeFile('legacy.xls', 'application/vnd.ms-excel'))).toBe(false);
    expect(isFileTypeSupported(makeFile('deck.pptx', ''))).toBe(false);
  });
});
