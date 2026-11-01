"""Floating glass menu chrome for the dashboard sidebar.

The radios, the theme switch, the market and the refresh toggle stay Streamlit widgets (they own the session).
This module only draws the rail around them: a collapsed icon pill, an expanded panel, search, tooltips and Stop.
Nothing here fetches, and an unknown open-trade count is omitted rather than shown as zero.
"""
from html import escape

from ui.client_script import client_script

TITLE = "AI Trading Floor"
SUBTITLE = "OANDA demo"
SEARCH = "Buscar páginas"

_LOGO = (
    '<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">'
    '<path d="M3 14.5h3.2V8.2H3v6.3zm5.3 0h3.2V4.8H8.3v9.7zm5.3 0H17V10h-3.4v4.5z" fill="#1a1405"/>'
    '</svg>'
)
_CHEVRON_RIGHT = (
    '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">'
    '<path d="M5 2.5L9.5 7 5 11.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
    '</svg>'
)
_CHEVRON_LEFT = (
    '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">'
    '<path d="M9 2.5L4.5 7 9 11.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
    '</svg>'
)
_SEARCH_ICON = (
    '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">'
    '<circle cx="7" cy="7" r="4.2" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    '<path d="M10.2 10.2L13.2 13.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
    '</svg>'
)

# Re-bind after every Streamlit rerun (the sidebar DOM is new) but listen only once.
_SCRIPT = r"""
(function(){
  function root(){return document.querySelector('.stApp')}
  function setOpen(open){
    var app=root(); if(!app) return;
    app.classList.toggle('aif-nav-open', !!open);
    try{sessionStorage.setItem('aif-nav', open?'open':'closed')}catch(e){}
    var bd=document.querySelector('.aif-backdrop');
    if(!bd){bd=document.createElement('div'); bd.className='aif-backdrop';}
    if(bd.parentNode!==app) app.appendChild(bd);
  }
  function applyStored(){
    var stored=null;
    try{stored=sessionStorage.getItem('aif-nav')}catch(e){}
    var open=stored==='open'||(stored!=='closed'&&window.innerWidth>=900);
    setOpen(open);
  }
  function tips(){
    var chrome=document.querySelector('.aif-chrome');
    var n=chrome&&chrome.getAttribute('data-open-trades');
    document.querySelectorAll('[data-testid="stSidebar"] [role="radiogroup"] label').forEach(function(lab){
      var text=(lab.innerText||'').replace(/\s+/g,' ').trim();
      if(n&&/Trades/.test(text)&&text.indexOf('Detalle')!==0&&!lab.querySelector('.aif-badge')){
        var badge=document.createElement('span');
        badge.className='aif-badge';
        badge.textContent=n;
        lab.appendChild(badge);
      }
      lab.setAttribute('data-tip', n&&/Trades/.test(text)&&text.indexOf('Detalle')!==0?text+' · '+n:text);
    });
  }
  if(!window.__aifNav){
    window.__aifNav=1;
    document.addEventListener('click', function(ev){
      var t=ev.target; if(!t||!t.closest) return;
      if(t.closest('.aif-expand, .aif-search-icon')){
        setOpen(true);
        if(t.closest('.aif-search-icon')){
          var field=document.querySelector('.aif-search');
          if(field) setTimeout(function(){field.focus()}, 320);
        }
        ev.preventDefault();
        return;
      }
      if(t.closest('.aif-collapse, .aif-backdrop')){setOpen(false); ev.preventDefault(); return}
      var stop=t.closest('.aif-stop');
      if(!stop) return;
      var buttons=document.querySelectorAll('button');
      for(var i=0; buttons.length>i; i++){
        if(buttons[i]!==stop && (buttons[i].innerText||'').trim()==='Stop'){buttons[i].click(); break}
      }
    });
    document.addEventListener('input', function(ev){
      if(!ev.target.classList||!ev.target.classList.contains('aif-search')) return;
      var q=ev.target.value.trim().toLowerCase();
      document.querySelectorAll('[data-testid="stSidebar"] [role="radiogroup"] > div').forEach(function(row){
        var lab=row.querySelector('label');
        var text=lab?(lab.innerText||'').toLowerCase():'';
        row.style.display=(!q||text.indexOf(q)!==-1)?'':'none';
      });
    });
  }
  applyStored();
  tips();
})();
"""


def open_trades_count():
    """How many OPEN demo trades are already in the cache, or None when that is unknown. Never fetches."""
    try:
        from ui import demo_refresher
        if not demo_refresher.running():
            return None
        from ui.floor_cache import peek_demo_trades
        data = peek_demo_trades(demo_refresher._epoch)
    except Exception:  # noqa: BLE001 - a missing cache is "no badge", not a broken menu
        return None
    if not isinstance(data, dict) or data.get("error") is not None or not isinstance(data.get("open"), (list, tuple)):
        return None
    return len(data["open"])


def section_css():
    """Tiny tracked group labels. Indexes follow the radio order in ``ui/app.py`` (luxury pages, Floor, then detail)."""
    from ui.views.luxury import PAGES
    mesa = len(PAGES) + 1
    detail = len(PAGES) + 2

    def rule(index, text):
        return (
            f'[data-testid="stSidebar"] [role="radiogroup"] > div:nth-child({index}){{position:relative;margin-top:28px}}'
            f'[data-testid="stSidebar"] [role="radiogroup"] > div:nth-child({index})::before{{content:"{text}";'
            f'position:absolute;top:-18px;left:12px;font-size:10px;font-weight:700;letter-spacing:.14em;'
            f'text-transform:uppercase;color:var(--aif-section,#a9b8b6);white-space:nowrap}}'
        )
    return rule(1, "PRINCIPAL") + rule(mesa, "MESA") + rule(detail, "DETALLE")


def _version():
    from ui.i18n_es import tr
    return tr("PAPER · V2")


def chrome(open_count):
    """Header of the rail: logo, title, the two chevrons and the search pill. No external assets."""
    count = ""
    if isinstance(open_count, int) and not isinstance(open_count, bool) and open_count > 0:
        count = f' data-open-trades="{open_count}"'
    return (
        f'<div class="aif-chrome"{count}>'
        f'<button type="button" class="aif-expand" aria-label="Abrir menú">{_CHEVRON_RIGHT}</button>'
        f'<div class="aif-brand"><span class="aif-logo">{_LOGO}</span>'
        f'<span class="aif-brand-text"><b>{escape(TITLE)}</b><small>{escape(SUBTITLE)}</small>'
        f'<small class="aif-version">{escape(_version())}</small></span>'
        f'<button type="button" class="aif-collapse" aria-label="Cerrar menú">{_CHEVRON_LEFT}</button></div>'
        f'<button type="button" class="aif-search-icon" aria-label="{escape(SEARCH)}">{_SEARCH_ICON}</button>'
        f'<label class="aif-search-wrap"><input class="aif-search" type="search" placeholder="{escape(SEARCH)}" '
        f'aria-label="{escape(SEARCH)}"></label></div>'
    )


def stop_button():
    """Clicks the real Streamlit Stop control when a run is in progress. It does not stop anything by itself."""
    return '<button type="button" class="aif-stop">Stop</button>'


def install(st):
    client_script(st, _SCRIPT)
