import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { S3Client } from '@aws-sdk/client-s3'
import { createS3Service } from '../../../../src/modules/storage/providers/s3-client'

function service() {
  return createS3Service({
    region: 'us-east-1',
    accessKeyId: 'AKIAIOSFODNN7EXAMPLE',
    secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
  })
}

function rejectWith(error: object): void {
  sinon.stub(S3Client.prototype, 'send').rejects(Object.assign(new Error('s3'), error))
}

async function codeOf(promise: Promise<unknown>): Promise<string | undefined> {
  try {
    await promise
  } catch (error) {
    return (error as { code?: string }).code
  }
  throw new Error('expected the call to fail')
}

describe('createS3Service', () => {
  afterEach(() => { sinon.restore() })

  describe('presigned URLs', () => {
    const checksumParams = ['x-amz-checksum-crc32', 'x-amz-sdk-checksum-algorithm', 'x-amz-checksum-mode']

    for (const [operation, params] of [
      ['putObject', { Bucket: 'b', Key: 'k', ContentType: 'application/pdf' }],
      ['uploadPart', { Bucket: 'b', Key: 'k', PartNumber: 1, UploadId: 'u-1' }],
      ['getObject', { Bucket: 'b', Key: 'k' }],
    ] as const) {
      it(`${operation} carries no checksum of the empty presign body`, async () => {
        const url = new URL(await service().getSignedUrlPromise(operation, { ...params, Expires: 60 }))

        const names = [...url.searchParams.keys()].map((name) => name.toLowerCase())
        expect(names.filter((name) => checksumParams.includes(name))).to.deep.equal([])
        expect(url.searchParams.get('X-Amz-Expires')).to.equal('60')
      })
    }
  })

  describe('error codes', () => {
    it('keeps NoSuchBucket on a 404 instead of reading it as a missing object', async () => {
      rejectWith({ name: 'NoSuchBucket', $metadata: { httpStatusCode: 404 } })

      expect(await codeOf(service().headObject({ Bucket: 'b', Key: 'k' }).promise())).to.equal('NoSuchBucket')
    })

    it('reads a HeadObject 404 as NotFound', async () => {
      rejectWith({ name: 'NotFound', $metadata: { httpStatusCode: 404 } })

      expect(await codeOf(service().headObject({ Bucket: 'b', Key: 'k' }).promise())).to.equal('NotFound')
    })

    it('reads an unnamed 404 as NotFound', async () => {
      rejectWith({ name: '', $metadata: { httpStatusCode: 404 } })

      expect(await codeOf(service().headObject({ Bucket: 'b', Key: 'k' }).promise())).to.equal('NotFound')
    })

    it('keeps NoSuchKey from a GetObject', async () => {
      rejectWith({ name: 'NoSuchKey', $metadata: { httpStatusCode: 404 } })

      expect(await codeOf(service().getObject({ Bucket: 'b', Key: 'k' }).promise())).to.equal('NoSuchKey')
    })

    it('leaves a code the error already has', async () => {
      rejectWith({ name: 'NoSuchBucket', code: 'AccessDenied', $metadata: { httpStatusCode: 403 } })

      expect(await codeOf(service().deleteObject({ Bucket: 'b', Key: 'k' }).promise())).to.equal('AccessDenied')
    })
  })
})
