// Apply a saved light/dark choice before the first paint, so pages don't flash.
// A separate file (not inline) so the content security policy can forbid inline scripts.
try {
  var t = localStorage.getItem('nis-theme')
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t
} catch {}
