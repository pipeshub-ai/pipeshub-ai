import { BadRequestError } from '../../../libs/errors/http.errors';
import { EmailTemplateType } from '../middlewares/types';
import {
  accountCreation,
  appUserInvite,
  connectorAccountConnect,
  domainLimitReached,
  emailChangeNotice,
  feedbackCopy,
  feedbackReceived,
  isEmailChangeNoticeData,
  isSandboxCapacityAlertData,
  joinRequestDecision,
  joinRequestNotify,
  loginWithOTPRequest,
  orgEmailVerification,
  resetEmail,
  resetPassword,
  sandboxCapacityAlert,
  sandboxCapacityAlertText,
  scheduledRunEnded,
  supportRequest,
  suspiciousLoginAttempt,
} from './emailTemplates';

/** Renders one template's data; throws when the data has the wrong shape. */
export type EmailTemplateRenderer = (
  templateData: Record<string, unknown>,
) => string;

const extraTemplates = new Map<string, EmailTemplateRenderer>();

/** Adds template types `getEmailContent` renders besides its own. */
export function useEmailTemplates(
  renderers: Readonly<Record<string, EmailTemplateRenderer>>,
): void {
  for (const [type, render] of Object.entries(renderers)) {
    extraTemplates.set(type, render);
  }
}

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

    case EmailTemplateType.ScheduledRunEnded as string:
      return scheduledRunEnded(templateData);

    case EmailTemplateType.SandboxCapacityAlert as string:
      if (!isSandboxCapacityAlertData(templateData)) {
        throw new BadRequestError(
          'sandboxCapacityAlert requires heading, intro, reasonText, actions, stats and docsUrl',
        );
      }
      return sandboxCapacityAlert(templateData);
    case EmailTemplateType.ConnectorAccountConnect as string:
      return connectorAccountConnect(templateData);

    // `as string`: the type is a plain string here, as for every case above.
    case EmailTemplateType.SupportRequest as string:
      return supportRequest(templateData);

    case EmailTemplateType.FeedbackReceived as string:
      return feedbackReceived(templateData);

    case EmailTemplateType.FeedbackCopy as string:
      return feedbackCopy(templateData);

    default: {
      const render = extraTemplates.get(emailTemplateType);
      if (render) {
        return render(templateData);
      }
      throw 'Unknown Template';
    }
  }
}

/**
 * The plain-text part for a template that has one, or `undefined` to send
 * HTML only. Called after `getEmailContent` accepted the same data.
 */
export function getEmailText(
  emailTemplateType: string,
  templateData: Record<string, unknown>,
): string | undefined {
  if (
    emailTemplateType === (EmailTemplateType.SandboxCapacityAlert as string) &&
    isSandboxCapacityAlertData(templateData)
  ) {
    return sandboxCapacityAlertText(templateData);
  }
  return undefined;
}
