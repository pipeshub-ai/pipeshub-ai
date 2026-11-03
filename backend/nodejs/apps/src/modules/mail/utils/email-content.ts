import { BadRequestError } from '../../../libs/errors/http.errors';
import { EmailTemplateType } from '../middlewares/types';
import {
  accountCreation,
  appUserInvite,
  domainLimitReached,
  emailChangeNotice,
  isEmailChangeNoticeData,
  joinRequestDecision,
  joinRequestNotify,
  loginWithOTPRequest,
  orgEmailVerification,
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

    case EmailTemplateType.EmailChangeNotice:
      // The notice names the person and the new address; a caller that
      // omits either would render a blank where a reader expects a fact.
      if (!isEmailChangeNoticeData(templateData)) {
        throw new BadRequestError(
          'emailChangeNotice requires name, orgName and newEmail',
        );
      }
      return emailChangeNotice(templateData);

    case EmailTemplateType.AppuserInvite:
      return appUserInvite(templateData);

    case EmailTemplateType.OrgEmailVerification:
      return orgEmailVerification(templateData);

    case EmailTemplateType.DomainLimitReached:
      return domainLimitReached(templateData);

    case EmailTemplateType.JoinRequestNotify:
      return joinRequestNotify(templateData);

    case EmailTemplateType.JoinRequestDecision:
      return joinRequestDecision(templateData);

    default:
      throw 'Unknown Template';
  }
}
