import axios from 'axios';
import { Logger } from '../services/logger.service';

interface TurnstileVerificationResponse {
  success: boolean;
  'error-codes'?: string[];
  challenge_ts?: string;
  hostname?: string;
}

/**
 * Verifies a Cloudflare Turnstile token
 * @param token - The Turnstile token to verify
 * @param secretKey - The Turnstile secret key
 * @param userIp - Optional user IP address for additional verification
 * @param logger - Optional logger instance
 * @returns Promise<boolean> - Returns true if verification succeeds, false otherwise
 */
export async function verifyTurnstileToken(
  token: string | undefined,
  secretKey: string | undefined,
  userIp?: string,
  logger?: Logger,
): Promise<boolean> {
  // If no token provided, return false
  if (!token) {
    if (logger) {
      logger.warn('Turnstile token is missing');
    }
    return false;
  }

  // If no secret key configured, skip verification (for development/testing)
  if (!secretKey) {
    if (logger) {
      logger.warn('Turnstile secret key is not configured. Skipping verification.');
    }
    return true; // Allow request to proceed if Turnstile is not configured
  }

  try {
    const requestData: {
      secret: string;
      response: string;
      remoteip?: string;
    } = {
      secret: secretKey,
      response: token,
    };

    // Include user IP if provided
    if (userIp) {
      requestData.remoteip = userIp;
    }

    const response = await axios.post<TurnstileVerificationResponse>(
      'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      requestData
    );

    const result = response.data;

    if (result.success) {
      if (logger) {
        logger.debug('Turnstile verification successful', {
          challenge_ts: result.challenge_ts,
          hostname: result.hostname,
        });
      }
      return true;
    } else {
      if (logger) {
        logger.warn('Turnstile verification failed', {
          'error-codes': result['error-codes'],
        });
      }
      return false;
    }
  } catch (error) {
    if (logger) {
      logger.error('Error verifying Turnstile token', { error });
    }
    // On error, fail securely by returning false
    return false;
  }
}


