// services/nodejs/apps/src/modules/storage/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Storage module Swagger configuration
 */
export const storageSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'storage',
  tagName: 'Storage',
  tagDescription:
    'Storage service operations for document management, versioning, and retrieval',
  yamlFilePath: path.join(process.cwd(), 'src/modules/storage/docs/swagger.yaml'),
  baseUrl: '/api/v1/document', // for storage module
};

/**
 * Function to register storage module Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerStorageSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(storageSwaggerConfig);
}
