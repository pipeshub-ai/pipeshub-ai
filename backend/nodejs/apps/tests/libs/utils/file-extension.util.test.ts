import { expect } from 'chai'
import {
  getFileExtension,
  getFilenameWithoutExtension,
} from '../../../src/libs/utils/file-extension.util'
import { getMimeType } from '../../../src/modules/storage/mimetypes/mimetypes'

describe('file-extension.util', () => {
  describe('getFileExtension', () => {
    it('derives the extension from the LAST dot (dots in the middle are fine)', () => {
      expect(getFileExtension('quarterly.report.2024.pdf')).to.equal('pdf')
      expect(getFileExtension('my.data.v2.csv')).to.equal('csv')
      expect(getFileExtension('archive.tar.gz')).to.equal('gz')
    })

    it('handles names whose base equals an extension (pdf.pdf, md.md)', () => {
      // Regression: these previously cascaded into an upload failure.
      expect(getFileExtension('pdf.pdf')).to.equal('pdf')
      expect(getFileExtension('md.md')).to.equal('md')
      expect(getFileExtension('csv.csv')).to.equal('csv')
    })

    it('lower-cases the extension', () => {
      expect(getFileExtension('REPORT.PDF')).to.equal('pdf')
    })

    it('returns null when there is no real extension', () => {
      expect(getFileExtension('README')).to.equal(null)
      expect(getFileExtension('.gitignore')).to.equal(null)
      expect(getFileExtension('trailingdot.')).to.equal(null)
      expect(getFileExtension('')).to.equal(null)
      expect(getFileExtension(undefined)).to.equal(null)
    })
  })

  describe('getFilenameWithoutExtension', () => {
    it('strips only the final extension', () => {
      expect(getFilenameWithoutExtension('quarterly.report.2024.pdf')).to.equal(
        'quarterly.report.2024',
      )
      expect(getFilenameWithoutExtension('pdf.pdf')).to.equal('pdf')
    })

    it('returns the name unchanged when there is no real extension', () => {
      expect(getFilenameWithoutExtension('README')).to.equal('README')
      expect(getFilenameWithoutExtension('.gitignore')).to.equal('.gitignore')
    })
  })

  describe('getMimeType (dot tolerance)', () => {
    it('resolves with or without a leading dot', () => {
      expect(getMimeType('pdf')).to.equal('application/pdf')
      expect(getMimeType('.pdf')).to.equal('application/pdf')
      expect(getMimeType('MD')).to.equal('text/markdown')
    })

    it('returns empty string for unknown / empty extensions', () => {
      expect(getMimeType('')).to.equal('')
      expect(getMimeType('totallyunknownext')).to.equal('')
    })

    it('resolves code file extensions', () => {
      expect(getMimeType('py')).to.equal('text/x-python')
      expect(getMimeType('ts')).to.equal('application/typescript')
      expect(getMimeType('go')).to.equal('text/x-go')
      expect(getMimeType('rs')).to.equal('text/x-rust')
      expect(getMimeType('sh')).to.equal('application/x-sh')
    })
  })
})
