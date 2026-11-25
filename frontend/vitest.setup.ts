/**
 * The one browser style a unit test's accessibility tree needs (CJ14-FU-3): a Radix Themes tab trigger renders a
 * second, sizing-only copy of its label that Radix's stylesheet hides with `visibility: hidden`. jsdom loads no
 * stylesheet, so a tab was named twice ("Jobs Jobs") and tests matched names by prefix. With the rule, testing
 * library names a tab as a browser does.
 */
if (typeof document !== 'undefined') {
  const style = document.createElement('style');
  style.textContent = '.rt-BaseTabListTriggerInnerHidden { visibility: hidden; }';
  document.head.appendChild(style);
}
