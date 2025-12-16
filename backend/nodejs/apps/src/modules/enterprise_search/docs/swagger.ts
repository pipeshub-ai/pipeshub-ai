// services/nodejs/apps/src/modules/enterprise_search/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Enterprise Search module Swagger configuration
 * Includes Conversations, Semantic Search, and Agents endpoints
 */
export const enterpriseSearchSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'enterprise-search',
  tagName: 'Enterprise Search',
  tagDescription:
    'Enterprise search operations including conversations, semantic search, and AI agents',
  yamlFilePath: path.join(process.cwd(), 'src/modules/enterprise_search/docs/swagger.yaml'),
  baseUrl: '/api/v1',
};

/**
 * Function to register enterprise search module Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerEnterpriseSearchSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(enterpriseSearchSwaggerConfig);
}

