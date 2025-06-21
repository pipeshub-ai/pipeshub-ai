export interface S3StorageConfig {
  jwtPrivateKey: string,
  accessKeyId: string,
  secretAccessKey: string,
  region: string,
  bucketName: string,
}
export interface AzureBlobStorageConfig {
  azureBlobConnectionString: string,
  endpointProtocol: string,
  accountName: string,
  accountKey: string,
  endpointSuffix: string,
  containerName: string,
}

export interface LocalStorageConfig {
  mountName: string,
  baseUrl: string
}