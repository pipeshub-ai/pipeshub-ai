import { addStyling } from './styling';
import Handlebars from 'handlebars';
import fs from 'fs';
const cwd = process.cwd();
const loadTemplate = (path: string): string => fs.readFileSync(`${cwd}/${path}`, 'utf8');

const loginTemplate = loadTemplate('src/modules/mail/views/layouts/user/login.hbs');
const suspiciousLoginTemplate = loadTemplate('src/modules/mail/views/layouts/user/suspiciousLogin.hbs');
const resetPasswordTemplate = loadTemplate('src/modules/mail/views/layouts/user/resetPassword.hbs');
const accountCreationTemplate = loadTemplate('src/modules/mail/views/layouts/org/accountCreation.hbs');
const appUsersInviteTemplate = loadTemplate('src/modules/mail/views/layouts/appusers/invite.hbs');
const headerTemplate = loadTemplate('src/modules/mail/views/partials/header.hbs');
const footerTemplate = loadTemplate('src/modules/mail/views/partials/footer.hbs');
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


const compileTemplate = (template: string, templateData:Record<string,any>): string => {
  if (!templateData) {
    throw new Error(`Invalid templateData ${JSON.stringify(templateData)}`);
  }
  const compiledTemplate = Handlebars.compile(template);
  return compiledTemplate(addStyling(templateData));
};

export const loginWithOTPRequest = (templateData: Record<string,any>): string => compileTemplate(loginTemplate, templateData);
export const suspiciousLoginAttempt = (templateData: Record<string,any>): string => compileTemplate(suspiciousLoginTemplate, templateData);
export const resetPassword = (templateData: Record<string,any>): string => compileTemplate(resetPasswordTemplate, templateData);
export const accountCreation = (templateData: Record<string,any>): string => compileTemplate(accountCreationTemplate, templateData);
export const appUserInvite = (templateData: Record<string,any>): string => compileTemplate(appUsersInviteTemplate, templateData);


