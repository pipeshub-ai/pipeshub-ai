import { expect } from 'chai';
import { loginWithOTPRequest, appUserInvite } from '../../../../src/modules/mail/utils/emailTemplates';
import { DEFAULT_BRAND, setBrandResolver } from '../../../../src/libs/branding';

const FOOTER =
  'Where your team and AI work together. PipesHub is a fully extensible and explainable workplace AI platform for enterprise search and workflow automation.';

const text = (html: string): string => html.replace(/\s+/g, ' ');

describe('email templates and the brand', () => {
  afterEach(() => setBrandResolver(() => DEFAULT_BRAND));

  it('render PipesHub, its support address and privacy link by default', () => {
    const login = text(loginWithOTPRequest({ name: 'Ada', otp: '123456' }));
    const invite = text(appUserInvite({ invitee: 'Bob', orgName: 'Acme', link: 'https://app.test/x' }));

    expect(login).to.include('Your PipesHub sign-in code');
    expect(login).to.include(FOOTER);
    expect(login).to.include('href="mailto:support@pipeshub.com"');
    expect(login).to.include('href="https://pipeshub.com/privacy-policy"');
    expect(invite).to.include('on PipesHub');
    expect(invite).to.include('>support@pipeshub.com</a>');
  });

  it('take the name, addresses and footer from a registered resolver', () => {
    setBrandResolver(() => ({
      ...DEFAULT_BRAND,
      productName: 'Acme Search',
      supportEmail: 'help@example.com',
      privacyUrl: null,
      footerBlurb: 'Acme footer.',
    }));

    const login = text(loginWithOTPRequest({ name: 'Ada', otp: '123456' }));

    expect(login).to.include('Your Acme Search sign-in code');
    expect(login).to.include('Acme footer.');
    expect(login).to.include('href="mailto:help@example.com"');
    expect(login).to.not.include('Privacy Policy');
    expect(login).to.not.include('PipesHub');
  });

  it('show the product name in the header, never a logo image', () => {
    setBrandResolver(() => ({ ...DEFAULT_BRAND, emailLogoUrl: 'https://cdn.example.com/logo.png' }) as typeof DEFAULT_BRAND);

    const login = text(loginWithOTPRequest({ name: 'Ada', otp: '123456' }));

    expect(login).to.not.include('logo.png');
    expect(login).to.include('PipesHub');
  });

  it('let a caller-supplied brand field win over the configured brand', () => {
    const login = text(loginWithOTPRequest({ name: 'Ada', otp: '123456', productName: 'Caller Product' }));

    expect(login).to.include('Your Caller Product sign-in code');
  });
});
