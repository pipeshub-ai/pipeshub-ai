import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  serveFileFromLocalStorage,
  getDocumentInfo,
  createPlaceholderDocument,
  generatePresignedUrlForDirectUpload,
} from '../../../../src/modules/storage/utils/utils'
import { NotFoundError, BadRequestError, InternalServerError } from '../../../../src/libs/errors/http.errors'
import { DocumentModel } from '../../../../src/modules/storage/schema/document.schema'
import mongoose from 'mongoose'
import fs from 'fs'

describe('storage/utils/utils - additional coverage', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('serveFileFromLocalStorage - file:// URL paths', () => {
    it('should handle direct file path (no file:// prefix)', () => {
      const doc = {
        local: { localPath: '/nonexistent/path/file.pdf' },
        documentName: 'test',
        extension: '.pdf',
      } as any

      const res = {
        setHeader: sinon.stub(),
        status: sinon.stub().returnsThis(),
        json: sinon.stub(),
      } as any

      try {
        serveFileFromLocalStorage(doc, res)
      } catch (error) {
        // Should throw NotFoundError because file doesn't exist
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should handle file:// URL on Unix-like systems', () => {
      const doc = {
        local: { localPath: 'file:///nonexistent/path/file.pdf' },
        documentName: 'test',
        extension: '.pdf',
      } as any

      const res = {
        setHeader: sinon.stub(),
        status: sinon.stub().returnsThis(),
        json: sinon.stub(),
      } as any

      try {
        serveFileFromLocalStorage(doc, res)
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should handle file:// URL with encoded characters', () => {
      const doc = {
        local: { localPath: 'file:///nonexistent/path/file%20name.pdf' },
        documentName: 'test',
        extension: '.pdf',
      } as any

      const res = {
        setHeader: sinon.stub(),
        status: sinon.stub().returnsThis(),
        json: sinon.stub(),
      } as any

      try {
        serveFileFromLocalStorage(doc, res)
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError when local property is null', () => {
      const doc = { local: null } as any
      try {
        serveFileFromLocalStorage(doc, {} as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })

    it('should throw NotFoundError when localPath is empty string', () => {
      const doc = { local: { localPath: '' } } as any
      try {
        serveFileFromLocalStorage(doc, {} as any)
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError)
      }
    })
  })

  describe('getDocumentInfo - additional paths', () => {
    it('should handle invalid ObjectId for documentId', async () => {
      const next = sinon.stub()
      const req = {
        params: { documentId: 'invalid-id' },
        user: { orgId: '507f1f77bcf86cd799439011' },
      } as any

      try {
        await getDocumentInfo(req, next)
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })

    it('should extract orgId from tokenPayload when user is not present', async () => {
      const next = sinon.stub()
      const mockDoc = { _id: 'doc-1', documentName: 'test' }
      sinon.stub(DocumentModel, 'findOne').resolves(mockDoc as any)

      const req = {
        params: { documentId: '507f1f77bcf86cd799439011' },
        tokenPayload: { orgId: '507f1f77bcf86cd799439012' },
      } as any

      const result = await getDocumentInfo(req, next)
      expect(result).to.exist
    })
  })

  describe('createPlaceholderDocument - additional paths', () => {
    it('should handle request with no userId (service request)', async () => {
      const savedDoc = { _id: 'doc-1', documentName: 'test' }
      sinon.stub(DocumentModel, 'create').resolves(savedDoc as any)
      const next = sinon.stub()

      const req = {
        tokenPayload: { orgId: '507f1f77bcf86cd799439011' },
        body: { documentName: 'test' },
      } as any

      const result = await createPlaceholderDocument(req, next, 1000, 'pdf', 'test.pdf')
      expect(result).to.exist
    })

    it('should use originalname for error messages when documentName is undefined', async () => {
      const next = sinon.stub()
      const req = {
        user: { orgId: '507f1f77bcf86cd799439011' },
        body: {},
      } as any

      try {
        await createPlaceholderDocument(req, next, 1000, 'xyz_invalid', 'uploaded.xyz_invalid')
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })

    it('should use "the file" as fallback when no originalname or documentName', async () => {
      const next = sinon.stub()
      const req = {
        user: { orgId: '507f1f77bcf86cd799439011' },
        body: {},
      } as any

      try {
        await createPlaceholderDocument(req, next, 1000, 'xyz_invalid')
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })

    it('should throw when document name contains forward slash', async () => {
      const next = sinon.stub()
      const req = {
        user: { orgId: '507f1f77bcf86cd799439011' },
        body: { documentName: 'path/name' },
      } as any

      try {
        await createPlaceholderDocument(req, next, 1000, 'pdf', 'test.pdf')
      } catch {
        // expected
      }
      expect(next.calledOnce).to.be.true
    })
  })

  describe('generatePresignedUrlForDirectUpload - additional paths', () => {
    it('should throw BadRequestError when documentPath is empty string', async () => {
      const mockAdapter = { generatePresignedUrlForDirectUpload: sinon.stub() }
      try {
        await generatePresignedUrlForDirectUpload(mockAdapter as any, '')
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError)
      }
    })
  })
})
