/**
 * The storage routes on their own, for the Python integration tests that read
 * ARTIFACTS_IT_STORAGE_URL (backend/python/tests/integration/compose/storage.yml
 * starts the stores; .github/workflows/backend-matrix.yml runs this).
 *
 * It serves `/api/v1/document` from the real router and controller on a real
 * MongoDB and one real file store. Everything the router would get from the rest
 * of the platform is a small in-memory stand-in: the key-value store, and the
 * configuration endpoint that hands out the storage settings.
 *
 *   MONGO_URL        MongoDB to store documents in
 *   SCOPED_SECRET    scoped-token secret; the Python side signs its tokens with it
 *   STORAGE_TYPE     local | s3 | azureBlob
 *   PORT             port to listen on (default 3000), on 127.0.0.1
 *   STORAGE_IT_NAME  names the bucket, container or mount (default artifact-contract-it)
 *   local:   HOME decides where files go (the provider writes under ~/.local)
 *   s3:      S3_ENDPOINT, S3_KEY, S3_SECRET; STORAGE_IT_S3_VHOST=1 leaves the
 *            SDK's own virtual-hosted <bucket>.s3.us-east-1.amazonaws.com
 *            addressing in place (S3_ENDPOINT is then used only to create the
 *            bucket); otherwise requests go path-style to S3_ENDPOINT
 *   azureBlob: AZURE_CONNECTION_STRING
 *
 * Prints `READY <origin>` once it is serving.
 */
import 'reflect-metadata'
import mongoose from 'mongoose'
import { DocumentModel } from '../../src/modules/storage/schema/document.schema'
import { required, startStorageApp } from './storage-stack'

async function main(): Promise<void> {
  const storageType = required('STORAGE_TYPE')
  const scopedSecret = required('SCOPED_SECRET')
  const name = process.env.STORAGE_IT_NAME || 'artifact-contract-it'

  await mongoose.connect(required('MONGO_URL'))
  await DocumentModel.syncIndexes()
  const stack = await startStorageApp({ storageType, scopedSecret, name, port: Number(process.env.PORT || 3000) })
  process.stdout.write(`READY ${stack.origin}\n`)
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`)
  process.exit(1)
})
