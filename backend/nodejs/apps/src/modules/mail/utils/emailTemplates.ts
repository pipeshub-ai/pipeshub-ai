import { addStyling } from './styling';
import Handlebars from 'handlebars';
import fs from 'fs';
import { getBrandConfig } from '../../../libs/branding';
const cwd = process.cwd();
export const loadTemplate = (path: string): string => fs.readFileSync(`${cwd}/${path}`, 'utf8');

const loginTemplate = loadTemplate(
  'src/modules/mail/views/layouts/user/login.hbs',
);
const suspiciousLoginTemplate = loadTemplate(
  'src/modules/mail/views/layouts/user/suspiciousLogin.hbs',
);
const resetPasswordTemplate = loadTemplate(
  'src/modules/mail/views/layouts/user/resetPassword.hbs',
);
const resetEmailTemplate = loadTemplate(
  'src/modules/mail/views/layouts/user/resetEmail.hbs',
);
const emailChangeNoticeTemplate = loadTemplate(
  'src/modules/mail/views/layouts/user/emailChangeNotice.hbs',
);
const accountCreationTemplate = loadTemplate(
  'src/modules/mail/views/layouts/org/accountCreation.hbs',
);
const domainLimitReachedTemplate = loadTemplate(
  'src/modules/mail/views/layouts/org/domainLimitReached.hbs',
);
const appUsersInviteTemplate = loadTemplate(
  'src/modules/mail/views/layouts/appusers/invite.hbs',
);
const joinRequestNotifyTemplate = loadTemplate(
  'src/modules/mail/views/layouts/appusers/joinRequestNotify.hbs',
);
const joinRequestDecisionTemplate = loadTemplate(
  'src/modules/mail/views/layouts/appusers/joinRequestDecision.hbs',
);
const connectorAccountConnectTemplate = loadTemplate(
  'src/modules/mail/views/layouts/connectors/connectAccount.hbs',
);
const orgEmailVerificationTemplate = loadTemplate(
  'src/modules/mail/views/layouts/org/orgEmailVerification.hbs',
);
const scheduledRunEndedTemplate = loadTemplate(
  'src/modules/mail/views/layouts/user/scheduledRunEnded.hbs',
);
const sandboxCapacityAlertTemplate = loadTemplate(
  'src/modules/mail/views/layouts/system/sandboxCapacityAlert.hbs',
);
const supportRequestTemplate = loadTemplate(
  'src/modules/mail/views/layouts/support/supportRequest.hbs',
);
const feedbackReceivedTemplate = loadTemplate(
  'src/modules/mail/views/layouts/support/feedbackReceived.hbs',
);
const feedbackCopyTemplate = loadTemplate(
  'src/modules/mail/views/layouts/support/feedbackCopy.hbs',
);
const headerTemplate = loadTemplate(
  'src/modules/mail/views/partials/header.hbs',
);
const footerTemplate = loadTemplate(
  'src/modules/mail/views/partials/footer.hbs',
);
const headTemplate = loadTemplate('src/modules/mail/views/partials/head.hbs');
const taskTableTemplate = loadTemplate('src/modules/mail/views/partials/taskTable.hbs');

Handlebars.registerHelper(
  "checkIfLengthLT5",
  function (this: any, str: string | null, options: Handlebars.HelperOptions) {
    if (!str) {
      return options.inverse(this);
    }
    if (str.length < 5) {
      return options.fn(this);
    } else {
      return options.inverse(this);
    }
  }
);

Handlebars.registerHelper(
  "checkIfLengthGTE5",
  function (this: any, str: string | null, options: Handlebars.HelperOptions) {
    if (!str) {
      return options.fn(this);
    }
    if (str.length >= 5) {
      return options.fn(this);
    } else {
      return options.inverse(this);
    }
  }
);

Handlebars.registerPartial('header', headerTemplate);
Handlebars.registerPartial('footer', footerTemplate);
Handlebars.registerPartial('head', headTemplate);
Handlebars.registerPartial('tasktable', taskTableTemplate);


// Every brand field is template data, so a build's own partials can use more of them.
// A field the caller set explicitly wins over the brand default.
function withBrand(
  templateData: Record<string, unknown>,
): Record<string, unknown> {
  return { ...getBrandConfig(), ...templateData };
}

export const compileTemplate = (template: string, templateData:Record<string,any>): string => {
  if (!templateData) {
    throw new Error(`Invalid templateData ${JSON.stringify(templateData)}`);
  }
  const compiledTemplate = Handlebars.compile(template);
  return compiledTemplate(addStyling(withBrand(templateData)));
};

export const loginWithOTPRequest = (templateData: Record<string,any>): string => compileTemplate(loginTemplate, templateData);
export const suspiciousLoginAttempt = (templateData: Record<string,any>): string => compileTemplate(suspiciousLoginTemplate, templateData);
export const resetPassword = (templateData: Record<string,any>): string => compileTemplate(resetPasswordTemplate, templateData);
export const resetEmail = (templateData: Record<string,any>): string => compileTemplate(resetEmailTemplate, templateData);
export interface EmailChangeNoticeData {
  name: string;
  orgName: string;
  newEmail: string;
}
export const isEmailChangeNoticeData = (
  data: unknown,
): data is EmailChangeNoticeData => {
  if (typeof data !== 'object' || data === null) {
    return false;
  }
  const record = data as Record<string, unknown>;
  return (
    typeof record.name === 'string' &&
    typeof record.orgName === 'string' &&
    typeof record.newEmail === 'string'
  );
};
export const emailChangeNotice = (
  templateData: EmailChangeNoticeData,
): string => compileTemplate(emailChangeNoticeTemplate, templateData);
export const accountCreation = (templateData: Record<string, any>): string =>
  compileTemplate(accountCreationTemplate, templateData);
export const appUserInvite = (templateData: Record<string, any>): string =>
  compileTemplate(appUsersInviteTemplate, templateData);
export const orgEmailVerification = (
  templateData: Record<string, any>,
): string => compileTemplate(orgEmailVerificationTemplate, templateData);
export const domainLimitReached = (templateData: Record<string, any>): string =>
  compileTemplate(domainLimitReachedTemplate, templateData);
export const joinRequestNotify = (templateData: Record<string, any>): string =>
  compileTemplate(joinRequestNotifyTemplate, templateData);
export const joinRequestDecision = (
  templateData: Record<string, any>,
): string => compileTemplate(joinRequestDecisionTemplate, templateData);
export const scheduledRunEnded = (
  templateData: Record<string, unknown>,
): string => compileTemplate(scheduledRunEndedTemplate, templateData);

export interface SandboxCapacityAlertStat {
  label: string;
  value: string;
}
export interface SandboxCapacityAlertData {
  heading: string;
  intro: string;
  reasonText: string;
  indexingText?: string;
  actions: string[];
  stats: SandboxCapacityAlertStat[];
  docsUrl: string;
  /** The closing note; says how often this email may come. Older jobs lack it. */
  footerNote?: string;
}
export const isSandboxCapacityAlertData = (
  data: unknown,
): data is SandboxCapacityAlertData => {
  if (typeof data !== 'object' || data === null) {
    return false;
  }
  const record = data as Record<string, unknown>;
  return (
    typeof record.heading === 'string' &&
    typeof record.intro === 'string' &&
    typeof record.reasonText === 'string' &&
    (record.indexingText === undefined ||
      typeof record.indexingText === 'string') &&
    Array.isArray(record.actions) &&
    record.actions.every((action) => typeof action === 'string') &&
    Array.isArray(record.stats) &&
    record.stats.every(
      (stat) =>
        typeof stat === 'object' &&
        stat !== null &&
        typeof (stat as Record<string, unknown>).label === 'string' &&
        typeof (stat as Record<string, unknown>).value === 'string',
    ) &&
    typeof record.docsUrl === 'string' &&
    (record.footerNote === undefined || typeof record.footerNote === 'string')
  );
};
export const SANDBOX_CAPACITY_DEFAULT_FOOTER =
  'Admins are notified in the app each time code runs are held back again; this email is sent at most once a day for each level. It goes to each admin of this workspace.';
/** The plain-text part of the code sandbox load alert email, with the same content as the HTML one. */
export const sandboxCapacityAlertText = (
  data: SandboxCapacityAlertData,
): string => {
  const lines: string[] = [
    data.heading,
    '',
    data.intro,
    '',
    `Why runs were held back: ${data.reasonText}`,
  ];
  if (data.indexingText) {
    lines.push(
      '',
      `Already done: ${data.indexingText} Runs were still held back after that, so the machine is short of capacity for this load.`,
    );
  }
  lines.push('', 'What you can do:');
  for (const action of data.actions) lines.push(`- ${action}`);
  lines.push(`- Read the load alerts guide: ${data.docsUrl}`, '');
  for (const stat of data.stats) lines.push(`${stat.label}: ${stat.value}`);
  lines.push('', data.footerNote ?? SANDBOX_CAPACITY_DEFAULT_FOOTER);
  return `${lines.join('\n')}\n`;
};
export const sandboxCapacityAlert = (
  templateData: SandboxCapacityAlertData,
): string => compileTemplate(sandboxCapacityAlertTemplate, { ...templateData });
export const connectorAccountConnect = (
  templateData: Record<string, unknown>,
): string => compileTemplate(connectorAccountConnectTemplate, templateData);
export const supportRequest = (templateData: Record<string, unknown>): string =>
  compileTemplate(supportRequestTemplate, templateData);
export const feedbackReceived = (
  templateData: Record<string, unknown>,
): string => compileTemplate(feedbackReceivedTemplate, templateData);
export const feedbackCopy = (templateData: Record<string, unknown>): string =>
  compileTemplate(feedbackCopyTemplate, templateData);
