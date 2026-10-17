export interface S3StorageConfig {
  jwtPrivateKey: string,
  // Optional: when omitted, the AWS SDK falls back to its default credential
  // provider chain (env vars, shared config, ECS task role, EC2 IAM role).
  accessKeyId?: string,
  secretAccessKey?: string,
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