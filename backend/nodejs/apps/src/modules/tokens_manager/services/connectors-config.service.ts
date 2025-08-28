import {
  ConfigurationManagerCommandOptions,
  ConfigurationManagerResponse,
  ConfigurationManagerServiceCommand,
} from '../../../libs/commands/configuration_manager/cm.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import {
  GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_CONFIG_PATH,
  GOOGLE_WORKSPACE_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH,
  ATLASIAN_CONFIG_PATH,
  ONE_DRIVE_CONFIG_PATH,
  SHAREPOINT_CONFIG_PATH,
  } from '../consts/constants';
import { generateFetchConfigToken } from '../utils/generateToken';

export const getGoogleWorkspaceBusinessCredentials = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }

  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH}`,
      method: HttpMethod.GET,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getGoogleWorkspaceIndividualCredentials = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }

  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH}`,
      method: HttpMethod.GET,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const setGoogleWorkspaceBusinessCredentials = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }

  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_CREDENTIALS_PATH}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
      body: req.body,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const deleteGoogleWorkspaceCredentials = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }

  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH}`,
      method: HttpMethod.DELETE,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getGoogleWorkspaceConfig = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_CONFIG_PATH}`,
      method: HttpMethod.GET,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getAtlassianOauthConfig = async (
  req: AuthenticatedUserRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${ATLASIAN_CONFIG_PATH}`,
      method: HttpMethod.GET,
      headers: req.headers as Record<string, string>,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getOneDriveConfig = async (
  req: AuthenticatedUserRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${ONE_DRIVE_CONFIG_PATH}`,
      method: HttpMethod.GET,
      headers: req.headers as Record<string, string>,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getSharePointConfig = async (

  req: AuthenticatedUserRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  } 
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${SHAREPOINT_CONFIG_PATH}`,
      method: HttpMethod.GET,
      headers: req.headers as Record<string, string>,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const setOneDriveConfig = async ( 
  req: AuthenticatedUserRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${ONE_DRIVE_CONFIG_PATH}`,
      method: HttpMethod.POST,
      headers: req.headers as Record<string, string>,
      body: req.body,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const setSharePointConfig = async (
  req: AuthenticatedUserRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${SHAREPOINT_CONFIG_PATH}`,
      method: HttpMethod.POST,
      headers: req.headers as Record<string, string>,
      body: req.body,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute(); 
  return response;
};

export const setGoogleWorkspaceConfig = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_CONFIG_PATH}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
      body: req.body,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const setAtlassianOauthConfig = async (
  req: AuthenticatedUserRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${ATLASIAN_CONFIG_PATH}`,
      method: HttpMethod.POST,
      headers: req.headers as Record<string, string>,
      body: req.body,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const setGoogleWorkspaceIndividualCredentials = async (
  req: AuthenticatedUserRequest,
  url: string,
  scopedJwtSecret: string,
  access_token: string,
  refresh_token: string,
  access_token_expiry_time: number,
  refresh_token_expiry_time?: number,
  enableRealTimeUpdates?: any,
  topicName?: string | null,
): Promise<ConfigurationManagerResponse> => {
  if (!req.user) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_CREDENTIALS_PATH}`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigToken(req.user, scopedJwtSecret)}`,
        'Content-Type': 'application/json',
      },
      body: {
        access_token,
        refresh_token,
        enableRealTimeUpdates,
        topicName,
        access_token_expiry_time,
        ...(refresh_token_expiry_time !== undefined && {
          refresh_token_expiry_time,
        }),
      },
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getRefreshTokenCredentials = async (
  req: AuthenticatedServiceRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.tokenPayload) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH}`,
      method: HttpMethod.GET,
      headers: req.headers as Record<string, string>,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const getRefreshTokenConfig = async (
  req: AuthenticatedServiceRequest,
  url: string,
): Promise<ConfigurationManagerResponse> => {
  if (!req.tokenPayload) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_CONFIG_PATH}`,
      method: HttpMethod.GET,
      headers: req.headers as Record<string, string>,
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};

export const setRefreshTokenCredentials = async (
  req: AuthenticatedServiceRequest,
  url: string,
  access_token: string,
  refresh_token: string,
  access_token_expiry_time: number,
  refresh_token_expiry_time?: number,
  enableRealTimeUpdates?: any,
  topicName?: string | null,
): Promise<ConfigurationManagerResponse> => {
  if (!req.tokenPayload) {
    throw new NotFoundError('User Not Found');
  }
  const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${url}/${GOOGLE_WORKSPACE_CREDENTIALS_PATH}`,
      method: HttpMethod.POST,
      headers: req.headers as Record<string, string>,
      body: {
        access_token,
        refresh_token,
        enableRealTimeUpdates,
        topicName,
        access_token_expiry_time,
        ...(refresh_token_expiry_time !== undefined && {
          refresh_token_expiry_time,
        }),
      },
    };

  const cmCommand = new ConfigurationManagerServiceCommand(
    configurationManagerCommandOptions,
  );
  const response = await cmCommand.execute();
  return response;
};
