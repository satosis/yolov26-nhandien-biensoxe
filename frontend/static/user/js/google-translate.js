function googleTranslateElementInit() {
  new google.translate.TranslateElement({
    pageLanguage: 'vi', includedLanguages: 'vi,zh-CN,en', autoDisplay: false
  }, 'google_translate_element');
}
function _setCookie(name, value, days, domain) {
  const exp = new Date(Date.now() + days * 864e5).toUTCString();
  let c = name + '=' + value + '; expires=' + exp + '; path=/; SameSite=Lax';
  if (domain) c += '; domain=' + domain;
  document.cookie = c;
}
function _delCookie(name, domain) {
  let c = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/';
  if (domain) c += '; domain=' + domain;
  document.cookie = c;
}
function setGoogleLang(langCode) {
  if (langCode === 'vi') {
    _delCookie('googtrans'); _delCookie('googtrans', location.hostname);
    _delCookie('lang_pref'); _delCookie('lang_pref', location.hostname);
  } else {
    const val = '/vi/' + langCode;
    _setCookie('googtrans', val, 7); _setCookie('googtrans', val, 7, location.hostname);
    _setCookie('lang_pref', langCode, 7); _setCookie('lang_pref', langCode, 7, location.hostname);
  }
  location.reload();
}
document.addEventListener('alpine:init', () => {
  const m = document.cookie.match(/lang_pref=([^;]+)/);
  if (m && m[1] && m[1] !== 'vi') { Alpine.store('i18n').lang = 'zh'; }
});
  
