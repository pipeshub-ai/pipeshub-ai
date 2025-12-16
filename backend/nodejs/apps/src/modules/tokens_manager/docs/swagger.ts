// services/nodejs/apps/src/modules/tokens_manager/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Connector Manager service Swagger configuration
 */
export const connectorManagerSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'connector-manager',
  tagName: 'Connector Manager',
  tagDescription:
    'Connector management service operations for third-party integrations, OAuth flows, record management, and data synchronization',
  yamlFilePath: path.join(process.cwd(), 'src/modules/tokens_manager/docs/swagger.yaml'),
  baseUrl: '/api/v1/connectors',
};

/**
 * Function to register connector manager service Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerConnectorManagerSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(connectorManagerSwaggerConfig);
}

