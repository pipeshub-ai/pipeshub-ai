import jwt, { JwtPayload } from 'jsonwebtoken';
import axios from 'axios';
import jwkToPem from 'jwk-to-pem';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { Logger } from '../../../libs/services/logger.service';
const logger = Logger.getInstance({
  service: 'Azure Ad Token Validation',
});

export const validateAzureAdUser = async (
  credentials: Record<string, any>,
  tenantId: string,
): Promise<any | null> => {
  try {
    const idToken = credentials?.idToken;
    if (!idToken) {
      throw new BadRequestError('Id token is required');
    }

    // Decode token without verification
    const decoded = jwt.decode(idToken, { complete: true });
    if (!decoded || !decoded.header)
      throw new UnauthorizedError('Invalid token structure');

    // if (handleAzureAuthCallback(credentials, email, decoded) == null) {
    //   return { statusCode: 400 };
    if (handleAzureAuthCallback(credentials, decoded) == null) {
      throw new BadRequestError('Error in Azure Auth CallBack');
    }

    // Fetch OpenID Configuration & JWKS
    const openIdConfig = await axios.get(
      `https://login.microsoftonline.com/${tenantId}/v2.0/.well-known/openid-configuration`,
    );

    const jwks = await axios.get(openIdConfig.data.jwks_uri);

    // Find the matching signing key
    const signingKey = jwks.data.keys.find(
      (key: any) => key.kid === decoded.header.kid,
    );
    if (!signingKey) throw new BadRequestError('Signing key not found');
    // Convert JWK to PEM & verify token
    const publicKey = jwkToPem(signingKey);
    const verifiedToken = jwt.verify(idToken, publicKey, {
      algorithms: ['RS256'],
    });

    return verifiedToken;
  } catch (error) {
    throw error;
  }
};

export const handleAzureAuthCallback = async (
  credentials: Record<string, any>,
  decoded: Record<string, any>,
) => {
  try {
    const accessToken = credentials?.accessToken;
    if (!accessToken) {
      return null;
    }
    const isJwtToken = (token: string) => token?.split('.')?.length === 3;
    let decodedToken;

    if (isJwtToken(accessToken)) {
      try {
        decodedToken = jwt.decode(accessToken) as JwtPayload;
      } catch (error) {
        if (error instanceof Error) {
          logger.error('Error decoding access token:', error.message);
        }
        return null;
      }
    } else {
      logger.warn('Personal account detected.');
      return accessToken;
    }

    const userPrincipalName = (
      decodedToken?.upn ||
      decoded?.payload?.email ||
      credentials.account?.username ||
      ''
    ).toLowerCase();
    if (!userPrincipalName) {
      // return res.status(400).json({ error: "User principal name (UPN) is missing." });
      logger.error('User principal name (UPN) is missing.');
      return null;
    }

    logger.info('UPN:', userPrincipalName);
    return accessToken;
    // const user = await User.findOne({
    //   upn: userPrincipalName,
    //   isDeleted: false,
    //   isAccountNotVerified: { $in: [false, undefined] },
    // }).lean();

    // if (!user) {
    //   return res.status(404).json({ error: "User not found." });
    // }


    // if (validateAzureUser(response, decodedToken, user.email, user.upn, res)) {
    //   return;
    // }
  } catch (error) {
    throw error;
  }
};

// const validateAzureAdUserEmail = (username:string, decodedToken:JwtPayload, userEmail:string) => {
//   const azureEmail = username;
//   const azureUPN = decodedToken?.upn;

//   if (!azureEmail) {
//      res.status(400).json({ error: "Azure email is missing." });
//   }

//   if (!azureUPN) {
//     if (azureEmail !== userEmail) {
//       return res.status(400).json({ error: "Email mismatch between Azure and system." });
//     }
//     if (userUPN && userEmail !== userUPN) {
//       return res.status(400).json({ error: "UPN mapping mismatch." });
//     }
//   } else {
//     if (userUPN && azureUPN !== userUPN) {
//       return res.status(400).json({ error: "UPN mismatch between Azure and system." });
//     }
//     if (azureEmail !== userEmail) {
//       return res.status(400).json({ error: "Email mismatch between Azure and system." });
//     }
//   }
//   return false;
// };
