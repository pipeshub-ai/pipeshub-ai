import test from 'node:test';
import * as assert from 'node:assert/strict';
import { classifyWindowOpen, isAppUrl, isExternalWebUrl } from '../navigation';

test('app:// URLs stay in the window', () => {
  assert.equal(isAppUrl('app://./login/', 'app'), true);
  assert.equal(isAppUrl('app://./chat/?x=1', 'app'), true);
});

test('server and other-scheme URLs do not', () => {
  assert.equal(isAppUrl('http://localhost:3001/login?saml_error=auth_failed', 'app'), false);
  assert.equal(isAppUrl('https://pipeshub.example.com/api/v1/saml/signIn', 'app'), false);
  assert.equal(isAppUrl('file:///etc/passwd', 'app'), false);
  assert.equal(isAppUrl('not a url', 'app'), false);
});

test('only http(s) is handed to the browser', () => {
  assert.equal(isExternalWebUrl('https://okta.example.com/sso'), true);
  assert.equal(isExternalWebUrl('http://localhost:3000/'), true);
  assert.equal(isExternalWebUrl('file:///C:/Windows/system32/calc.exe'), false);
  assert.equal(isExternalWebUrl('javascript:alert(1)'), false);
  assert.equal(isExternalWebUrl(''), false);
});

test('new-window requests for app pages load in the main window', () => {
  assert.equal(classifyWindowOpen('app://./record/abc-123?orgId=o1', 'app'), 'in-app');
});

test('new-window requests for web and mail links go to the OS', () => {
  assert.equal(classifyWindowOpen('https://docs.pipeshub.com/', 'app'), 'external');
  assert.equal(classifyWindowOpen('http://localhost:14000/record/abc', 'app'), 'external');
  assert.equal(classifyWindowOpen('mailto:support@example.com', 'app'), 'external');
  assert.equal(classifyWindowOpen('tel:+15550100', 'app'), 'external');
});

test('other new-window requests are refused', () => {
  assert.equal(classifyWindowOpen('file:///etc/passwd', 'app'), 'blocked');
  assert.equal(classifyWindowOpen('javascript:alert(1)', 'app'), 'blocked');
  assert.equal(classifyWindowOpen('not a url', 'app'), 'blocked');
});
