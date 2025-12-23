import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Knowledge Base module Swagger configuration
 */
export const knowledgeBaseSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'knowledge_base',
  tagName: 'Knowledge Base',
  tagDescription:
    'Knowledge Base service operations for managing knowledge bases, folders, records, and permissions',
  yamlFilePath: path.join(process.cwd(), 'src/modules/knowledge_base/docs/swagger.yaml'),
  baseUrl: '/api/v1/knowledgeBase', // for knowledge base module
};

/**
 * Function to register knowledge base module Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerKnowledgeBaseSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(knowledgeBaseSwaggerConfig);
}

