// services/nodejs/apps/src/modules/mail/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Mail service Swagger configuration
 */
export const mailSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'mail',
  tagName: 'Mail',
  tagDescription:
    'Mail service operations for sending emails with templates, SMTP configuration, and dynamic template management',
  yamlFilePath: path.join(process.cwd(), 'src/modules/mail/docs/swagger.yaml'),
  baseUrl: '/api/v1/mail', // for mail service
};

/**
 * Function to register mail service Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerMailSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(mailSwaggerConfig);
}

