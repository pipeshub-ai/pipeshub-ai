import passport from 'passport';
import {
  Strategy as SamlStrategy,
  Profile,
  VerifiedCallback,
} from '@node-saml/passport-saml';
import { Response, NextFunction, Request } from 'express';
import { AuthSessionRequest } from '../middlewares/types';
import { OrgAuthConfig } from '../schema/orgAuthConfiguration.schema';
import { Logger } from '../../../libs/services/logger.service';
import {
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { inject, injectable } from 'inversify';
import {
  ConfigurationManagerCommandOptions,
  ConfigurationManagerServiceCommand,
} from '../../../libs/commands/configuration_manager/cm.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { generateFetchConfigAuthToken } from '../utils/generateAuthToken';
import { AppConfig } from '../../tokens_manager/config/config';
import { samlSsoCallbackUrl, samlSsoConfigUrl } from '../constants/constants';
import { Org } from '../../user_management/schema/org.schema';
import { isValidEmail } from '../routes/saml.routes';

const orgIdToSamlEmailKey: Record<string, string> = {};
passport.serializeUser((user, done) => {
  done(null, user);
});

passport.deserializeUser((obj, done) => {
  if (obj) {
    done(null, obj);
  }
});
@injectable()
export class SamlController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('Logger') private logger: Logger,
  ) { }


  async updateSamlStrategiesWithCallback(): Promise<void> {


    const org = await Org.findOne({ isDeleted: false }).lean().exec();
    let user = { orgId: org?._id.toString(), _id: "" };
    if (!org) {
      throw new NotFoundError('Organization not found');
    }

    const configurationManagerCommandOptions: ConfigurationManagerCommandOptions =
    {
      uri: `${this.config.cmBackend}/${samlSsoConfigUrl}`,
      method: HttpMethod.GET,
      headers: {
        Authorization: `Bearer ${await generateFetchConfigAuthToken(
          user,
          this.config.scopedJwtSecret,
        )}`,
        'Content-Type': 'application/json',
      },
    };

    const getCredentialsCommand =
      new ConfigurationManagerServiceCommand(
        configurationManagerCommandOptions
      );


    const response = await getCredentialsCommand.execute();

    if (response.statusCode !== 200) {
      throw new InternalServerError(
        'Error getting saml credentials',
        response?.data?.error?.message,
      );
    }
    const credentialsData = response.data;
    if (!credentialsData.certificate) {
      throw new NotFoundError('Certificate is missing');
    }
    if (!credentialsData.entryPoint) {
      throw new NotFoundError('entryPoint is missing');
    }
    if (!credentialsData.emailKey) {
      throw new NotFoundError('email key is missing');
    }

    const samlCertificate = credentialsData.certificate;
    const samlEntryPoint = credentialsData.entryPoint;
    const samlEmailKey = credentialsData.emailKey;

    this.updateOrgIdToSamlEmailKey(org._id.toString(), samlEmailKey);
    this.updateSAMLStrategy(samlCertificate, samlEntryPoint);
  }

  // update the mapping
  updateOrgIdToSamlEmailKey(orgId: string, samlEmailKey: string) {
    orgIdToSamlEmailKey[orgId] = samlEmailKey;
  }

  // get the samlEmailKey by orgId
  getSamlEmailKeyByOrgId(orgId: string) {
    const entry = orgIdToSamlEmailKey[orgId];
    return entry ? entry : 'email';
  }

  b64DecodeUnicode(str: string) {
    return decodeURIComponent(
      atob(str)
        .split('')
        .map(function (c) {
          return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        })
        .join(''),
    );
  }

  updateSAMLStrategy(samlCertificate: string, samlEntryPoint: string) {
    passport.use(
      new (SamlStrategy as any)(
        {
          entryPoint: samlEntryPoint,
          callbackUrl: `${this.config.authBackend}/${samlSsoCallbackUrl}`,
          idpCert: samlCertificate,
          passReqToCallback: true,
          issuer: 'pipeshub',
          identifierFormat: null,
          wantAuthnResponseSigned: false,
          disableRequestedAuthnContext: true,
        },
        async (req: Request, profile: Profile, done: VerifiedCallback) => {
          try {
            const relayStateBase64 =
              (req as any).body?.RelayState || (req as any).query?.RelayState;
            const relayStateDecoded = relayStateBase64
              ? JSON.parse(
                Buffer.from(relayStateBase64, 'base64').toString('utf8'),
              )
              : {};

            // Attach additional metadata to profile
            (profile as any).orgId = relayStateDecoded.orgId;
            (profile as any).sessionToken = relayStateDecoded.sessionToken;

            return done(null, profile);
          } catch (err) {
            return done(err as Error);
          }
        },
        async (_req: Request, profile: Profile, done: VerifiedCallback) => {
          // Optional: Handle logout request here
          // For now, just pass profile through
          return done(null, profile);
        },
      ),
    );
  }

  async signInViaSAML(
    req: AuthSessionRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const email = req.query.email as string || "";
      const sessionToken = req.query.sessionToken as string;


      this.logger.debug(email);

      const orgAuthConfig = await OrgAuthConfig.findOne({

        isDeleted: false,
      }).lean().exec();

      if (!orgAuthConfig) {
        throw new NotFoundError('Organisation configuration not found');
      }

      const relayStateObj = { orgId: orgAuthConfig.orgId, sessionToken };
      const relayStateEncoded = Buffer.from(
        JSON.stringify(relayStateObj),
      ).toString('base64');
      req.query.RelayState = relayStateEncoded;

      passport.authenticate('saml', {
        failureRedirect: `/${this.config.frontendUrl}/auth/sign-in`,
        successRedirect: '/',
      })(req, res, next);
    } catch (error) {
      next(error);
    }
  }


  parseRelayState = (req: Request) => {
    const raw = (req.body?.RelayState || req.query?.RelayState) as string;
    if (!raw) return {};
    try {
      return JSON.parse(Buffer.from(raw, "base64").toString("utf8"));
    } catch {
      return {};
    }
  };

  // Helper: Extract validated email from SAML profile
  getSamlEmail = (samlUser: any, orgId: string): string | null => {
    // 1. Try the Organization's configured mapping first
    const configuredEmailKey = this.getSamlEmailKeyByOrgId(orgId);
    const primaryEmail = samlUser[configuredEmailKey];

    if (primaryEmail && isValidEmail(primaryEmail)) {
      return primaryEmail;
    }

    // 2. Fallback cascade if the configured key is missing or invalid
    const fallbackKeys = [
      "email", "mail", "userPrincipalName", "primaryEmail",
      "contactEmail", "preferred_username", "mailPrimaryAddress", "nameID"
    ];

    for (const key of fallbackKeys) {
      const val = samlUser[key];
      if (val && isValidEmail(val)) {
        return val;
      }
    }

    return null;
  };
}
