/**
 * Well-known free / consumer email providers.
 *
 * A login attempt whose domain matches one of these entries is treated as a
 * personal email: workspace lookup is done by exact email address rather than
 * by domain, so the user only sees orgs they already belong to.
 *
 * Business domains (anything NOT in this list) continue to use domain-based
 * workspace discovery.
 */
export const FREE_EMAIL_DOMAINS = new Set<string>([
  // Google
  'gmail.com',
  'googlemail.com',

  // Microsoft / Outlook family
  'outlook.com',
  'hotmail.com',
  'hotmail.co.uk',
  'hotmail.fr',
  'hotmail.de',
  'hotmail.es',
  'hotmail.it',
  'live.com',
  'live.co.uk',
  'live.fr',
  'live.de',
  'live.in',
  'msn.com',

  // Yahoo family
  'yahoo.com',
  'yahoo.co.uk',
  'yahoo.co.in',
  'yahoo.fr',
  'yahoo.de',
  'yahoo.es',
  'yahoo.it',
  'yahoo.com.au',
  'yahoo.com.br',
  'yahoo.com.ar',
  'yahoo.ca',
  'ymail.com',
  'rocketmail.com',

  // Apple
  'icloud.com',
  'me.com',
  'mac.com',

  // AOL / Verizon
  'aol.com',
  'aim.com',
  'verizon.net',

  // ProtonMail
  'protonmail.com',
  'protonmail.ch',
  'pm.me',
  'proton.me',

  // Tutanota
  'tutanota.com',
  'tutanota.de',
  'tutamail.com',
  'tuta.io',

  // GMX / Web.de
  'gmx.com',
  'gmx.de',
  'gmx.net',
  'gmx.at',
  'gmx.ch',
  'web.de',

  // Mail.com family
  'mail.com',
  'email.com',
  'usa.com',
  'myself.com',
  'consultant.com',
  'post.com',
  'europe.com',
  'asia.com',
  'writeme.com',
  'dr.com',
  'engineer.com',
  'fastservice.com',
  'accountant.com',

  // Zoho (free tier)
  'zoho.com',
  'zohomail.com',

  // Yandex
  'yandex.com',
  'yandex.ru',
  'ya.ru',

  // Mail.ru
  'mail.ru',
  'bk.ru',
  'inbox.ru',
  'list.ru',

  // Other widely-used free providers
  'rediffmail.com',
  'lycos.com',
  'excite.com',
  'inbox.com',
  'fastmail.com',
  'fastmail.fm',
  'hushmail.com',
  'guerrillamail.com',
  'sharklasers.com',
  'guerrillamailblock.com',
  'grr.la',
  'spam4.me',
  'discard.email',
  'mailnull.com',
  'trashmail.com',
  'trashmail.at',
  'trashmail.me',
  'throwam.com',
]);

/** Returns true when the domain belongs to a well-known free/consumer provider. */
export function isFreeEmailDomain(domain: string): boolean {
  return FREE_EMAIL_DOMAINS.has(domain.toLowerCase());
}
