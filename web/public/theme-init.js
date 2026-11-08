// Applies the stored or system theme before the first paint.
// Keep in sync with src/lib/theme.ts (storage key and attribute name).
(function () {
  var stored = null;
  try {
    stored = window.localStorage.getItem('msj.theme');
  } catch (error) {
    stored = null;
  }
  var theme =
    stored === 'light' || stored === 'dark'
      ? stored
      : window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light';
  var root = document.documentElement;
  root.setAttribute('data-theme', theme);
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#151618' : '#f4f2ec');
})();
