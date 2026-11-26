/**
 * Marks each icon font as ready once the browser has loaded it (`icons-outlined-ready`, `icons-filled-ready` on
 * <html>). Until then globals.css draws icons transparent: an icon font draws its glyph from the ligature text
 * ("refresh"), so a font still loading, slow or blocked (an offline install) would otherwise show clipped letters.
 */
const SCRIPT = `(function(){try{var d=document.documentElement,f=document.fonts,w={'Material Symbols Outlined':'icons-outlined-ready','Material Icons':'icons-filled-ready'};if(!f||!f.forEach){d.classList.add('icons-outlined-ready','icons-filled-ready');return}var mark=function(){f.forEach(function(x){var c=w[x.family.replace(/["']/g,'')];if(c&&x.status==='loaded')d.classList.add(c)})};mark();f.addEventListener&&f.addEventListener('loadingdone',mark);f.ready&&f.ready.then(mark)}catch(e){document.documentElement.classList.add('icons-outlined-ready','icons-filled-ready')}})()`;

export function IconFontScript() {
  return <script dangerouslySetInnerHTML={{ __html: SCRIPT }} />;
}
