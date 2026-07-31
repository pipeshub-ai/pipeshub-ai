import { EmailTemplateType } from '../middlewares/types';
import {
  accountCreation,
  appUserInvite,
  loginWithOTPRequest,
  resetEmail,
  resetPassword,
  suspiciousLoginAttempt,
} from './emailTemplates';

/** Renders a template by type. Throws for an unknown type — a caller bug. */
export function getEmailContent(
  emailTemplateType: string,
  templateData: Record<string, any>,
): string {
  switch (emailTemplateType) {
    case EmailTemplateType.LoginWithOtp:
      return loginWithOTPRequest(templateData);

    case EmailTemplateType.AccountCreation:
      return accountCreation(templateData);

    case EmailTemplateType.SuspiciousLoginAttempt:
      return suspiciousLoginAttempt(templateData);

    case EmailTemplateType.ResetPassword:
      return resetPassword(templateData);

    case EmailTemplateType.ResetEmail:
      return resetEmail(templateData);

    case EmailTemplateType.AppuserInvite:
      return appUserInvite(templateData);

    default:
      throw 'Unknown Template';
  }
}
