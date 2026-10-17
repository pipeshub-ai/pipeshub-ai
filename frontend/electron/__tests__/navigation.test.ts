import test from 'node:test';
import * as assert from 'node:assert/strict';
import { isAppUrl, isExternalWebUrl } from '../navigation';

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
