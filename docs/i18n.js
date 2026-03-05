// Shared i18n engine for PMBot docs
// Each page includes this script and defines its own STRINGS object,
// then calls initI18n(STRINGS).

function initI18n(strings) {
  const STORAGE_KEY = 'pmbot_docs_lang';
  let currentLang = localStorage.getItem(STORAGE_KEY) || 'en';

  function apply(lang) {
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const val = strings[lang] && strings[lang][key];
      if (val !== undefined) {
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
          el.placeholder = val;
        } else {
          el.innerHTML = val;
        }
      }
    });
    // Update toggle button state
    document.querySelectorAll('[data-lang-btn]').forEach(btn => {
      const l = btn.getAttribute('data-lang-btn');
      if (l === lang) {
        btn.classList.add('bg-brand', 'text-white');
        btn.classList.remove('text-gray-400');
      } else {
        btn.classList.remove('bg-brand', 'text-white');
        btn.classList.add('text-gray-400');
      }
    });
    // Update html lang attr
    document.documentElement.lang = lang === 'zh' ? 'zh-TW' : 'en';
  }

  // Wire toggle buttons
  document.querySelectorAll('[data-lang-btn]').forEach(btn => {
    btn.addEventListener('click', () => apply(btn.getAttribute('data-lang-btn')));
  });

  // Initial render
  apply(currentLang);
}
