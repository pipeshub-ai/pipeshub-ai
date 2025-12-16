import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Auth module Swagger configuration
 */
export const configurationManagerSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'configuration-manager',
  tagName: 'Configuration Manager',
  tagDescription:
    'Configuration Manager service operations for managing system configurations including storage, authentication, databases, connectors, AI models, and platform settings',
  yamlFilePath: path.join(process.cwd(), 'src/modules/configuration_manager/docs/swagger.yaml'),
  baseUrl: '/api/v1/configurationManager',
};

/**
 * Function to register configuration manager module Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerConfigurationManagerSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(configurationManagerSwaggerConfig);
}

