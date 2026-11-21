// QWeb plugins have their own document. Mirror the host's resolved palette as it changes.
(() => {
  const root = document.documentElement;
  const presets = {
    night: ['#080d14','#eef5f6','#9eafb6','#74868e','#0e1720','#15212b','#45d7d1','#20aaa5','#ee6ca7'],
    aqua: ['#05171a','#e8f7f5','#9bb9b5','#708e8c','#071d20','#0c2a2d','#32d6c5','#20a89c','#66a8d9'],
    light: ['#edf2f4','#202a31','#64727a','#87939a','#fff','#f0f5f7','#3269a8','#285887','#dc704e'],
    sakura: ['#f3e9ef','#332b35','#756875','#9a8995','#fffafc','#faeef4','#d84f82','#b63968','#6c83c9'],
  };
  const palette = ['app-bg','ink','muted','subtle','surface','surface-soft','brand','brand-strong','secondary'];
  const tokens = [...palette,'on-brand','brand-custom','brand-soft','line','line-strong','glass',
    'glass-strong','shadow','green','green-soft','coral','coral-soft','yellow','warm','app-font',
    'glass-blur','card-blur','content-blur'];
  const system = matchMedia('(prefers-color-scheme: dark)');
  const scheme = name => ['light','sakura'].includes(name) ? 'light' : 'dark';
  function standalone() {
    let settings = {};
    try { settings = JSON.parse(localStorage.getItem('qi-web:theme:v2')) || {}; } catch {}
    const name = Object.hasOwn(presets, settings.preset) ? settings.preset : system.matches ? 'night' : 'light';
    root.dataset.theme = name;
    root.style.colorScheme = scheme(name);
    root.dataset.motion = settings.motion === false ? 'off' : 'on';
    palette.forEach((token, index) => root.style.setProperty('--' + token, presets[name][index]));
    if (/^#[\da-f]{6}$/i.test(settings.accent)) root.style.setProperty('--brand', settings.accent);
    const hex = root.style.getPropertyValue('--brand').slice(1);
    const rgb = [0,2,4].map(i => parseInt(hex.slice(i,i+2),16) / 255)
      .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
    const bright = rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722;
    const defaults = {
      'on-brand': bright > .179 ? '#061112' : '#fff', 'line':'color-mix(in srgb,var(--ink) 14%,transparent)',
      'line-strong':'color-mix(in srgb,var(--ink) 28%,transparent)', 'brand-soft':'color-mix(in srgb,var(--brand) 14%,transparent)',
      'glass':'var(--surface)', 'glass-strong':'var(--surface)', green:'#50b887',coral:'#df6f66',yellow:'#edb74b',warm:'#e3a645',
    };
    Object.entries(defaults).forEach(([key,val]) => root.style.setProperty('--'+key,val));
  }
  let host;
  try { if (parent !== window) host = parent.document.documentElement; } catch {}
  if (!host || !parent.getComputedStyle(host).getPropertyValue('--app-bg').trim()) {
    standalone();
    system.addEventListener('change', standalone);
    window.addEventListener('storage', event => { if (event.key === 'qi-web:theme:v2') standalone(); });
    return;
  }
  function sync() {
    const css = parent.getComputedStyle(host);
    root.dataset.theme = host.dataset.theme || 'night';
    root.dataset.motion = host.dataset.motion || 'on';
    root.dataset.background = host.dataset.background || 'none';
    root.style.colorScheme = css.colorScheme;
    for (const key of tokens) {
      const value = css.getPropertyValue('--' + key).trim();
      if (value) root.style.setProperty('--' + key, value);
      else root.style.removeProperty('--' + key);
    }
  }
  sync();
  const observer = new MutationObserver(sync);
  observer.observe(host,{attributes:true,attributeFilter:['style','class','data-theme','data-motion','data-background']});
  window.addEventListener('pagehide', () => observer.disconnect(), {once:true});
  window.addEventListener('pageshow', e => { if (e.persisted) location.reload(); });
})();
