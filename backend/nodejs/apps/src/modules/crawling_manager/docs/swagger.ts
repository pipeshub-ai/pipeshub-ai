// services/nodejs/apps/src/modules/crawling_manager/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Crawling Manager module Swagger configuration
 */
export const crawlingManagerSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'crawling-manager',
  tagName: 'Crawling Manager',
  tagDescription:
    'Crawling Manager service operations for scheduling, managing, and monitoring data crawling jobs',
  yamlFilePath: path.join(
    process.cwd(),
    'src/modules/crawling_manager/docs/swagger.yaml',
  ),
  baseUrl: '/api/v1/crawlingManager',
};

/**
 * Function to register crawling manager module Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerCrawlingManagerSwagger(
  swaggerService: SwaggerService,
): void {
  swaggerService.registerModule(crawlingManagerSwaggerConfig);
}

