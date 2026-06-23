import { expect } from 'chai';
import {
  extensionToMimeType,
  getIndexableMimeTypes,
  getMimeType,
  indexableExtensions,
  isIndexableExtension,
} from '../../../../src/modules/storage/mimetypes/mimetypes';

/** Parity with supported_extensions in record.py, minus connector-only virtual types. */
const PYTHON_SUPPORTED_USER_EXTENSIONS = [
  'pdf',
  'docx',
  'doc',
  'xlsx',
  'xls',
  'csv',
  'html',
  'pptx',
  'ppt',
  'md',
  'mdx',
  'txt',
  'png',
  'jpg',
  'jpeg',
  'webp',
  'svg',
  'tsv',
  'py',
  'js',
  'jsx',
  'mjs',
  'cjs',
  'ts',
  'tsx',
  'java',
  'c',
  'h',
  'cpp',
  'cc',
  'cxx',
  'hpp',
  'hxx',
  'cs',
  'go',
  'rs',
  'rb',
  'php',
  'swift',
  'kt',
  'kts',
  'dart',
  'sh',
  'bash',
  'htm',
];

describe('storage/mimetypes/mimetypes', () => {
  describe('indexableExtensions', () => {
    it('matches Python supported_extensions minus sql_table/sql_view', () => {
      expect([...indexableExtensions].sort()).to.deep.equal(
        [...PYTHON_SUPPORTED_USER_EXTENSIONS].sort(),
      );
    });

    it('resolves every indexable extension to a non-empty MIME type', () => {
      for (const ext of indexableExtensions) {
        expect(getMimeType(ext), ext).to.not.equal('');
      }
    });
  });

  describe('isIndexableExtension', () => {
    it('accepts indexable extensions with or without a leading dot', () => {
      expect(isIndexableExtension('pdf')).to.be.true;
      expect(isIndexableExtension('.PDF')).to.be.true;
      expect(isIndexableExtension('md')).to.be.true;
      expect(isIndexableExtension('py')).to.be.true;
    });

    it('rejects known-but-non-indexable extensions', () => {
      expect(isIndexableExtension('exe')).to.be.false;
      expect(isIndexableExtension('zip')).to.be.false;
      expect(isIndexableExtension('mp3')).to.be.false;
      expect(isIndexableExtension('xyz')).to.be.false;
    });

    it('rejects empty extension', () => {
      expect(isIndexableExtension('')).to.be.false;
    });
  });

  describe('getIndexableMimeTypes', () => {
    it('returns deduplicated MIME types for indexable extensions', () => {
      const mimeTypes = getIndexableMimeTypes();
      expect(mimeTypes.length).to.equal(new Set(mimeTypes).size);
      expect(mimeTypes).to.include('application/pdf');
      expect(mimeTypes).to.include('text/markdown');
    });

    it('is a strict subset of the broad extensionToMimeType registry', () => {
      const broadMimeTypes = new Set(Object.values(extensionToMimeType));
      for (const mime of getIndexableMimeTypes()) {
        expect(broadMimeTypes.has(mime), mime).to.be.true;
      }
    });

    it('excludes MIME types for non-indexable registry entries such as exe', () => {
      const indexableMimeTypes = getIndexableMimeTypes();
      expect(indexableMimeTypes).to.not.include(
        extensionToMimeType.exe,
      );
    });
  });
});
