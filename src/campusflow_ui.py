"""Small, shared visual vocabulary for the Streamlit product surface.

The palette is inspired by Tianjin University's documented "北洋蓝" school
colour.  These product hex values are not presented as the university's
official VI values.  The route mark is an original CampusFlow decoration.
"""


from pathlib import Path

CAMPUSFLOW_THEME_CSS = '<style>' + Path(__file__).with_name('workspace.css').read_text(encoding='utf8') + '</style>'


BRAND_HEADER_HTML = """
<div class="cf-top-shell">
  <header class="cf-brand-header">
    <div class="cf-brand-lockup">
      <div class="cf-brand-mark" aria-hidden="true">
        <svg viewBox="0 0 48 48" focusable="false">
          <path d="M10 36 V29 Q10 24 15 24 H29 Q36 24 36 17 V10"/>
          <circle cx="10" cy="36" r="3.5"/><circle cx="24" cy="24" r="3.5"/><circle cx="36" cy="10" r="3.5"/>
        </svg>
      </div>
      <div>
        <div class="cf-brand-name">CampusFlow</div>
        <div class="cf-brand-subtitle">tjuer专属的校园时空规划系统</div>
      </div>
    </div>
  </header>
</div>
"""


# A narrow accessibility bridge for the native-widget settings drawer. It
# neither owns form values nor submits data; Escape clicks the existing close
# button. The observer is replaced on rerun, never accumulated.
SETTINGS_ACCESSIBILITY_HTML = """
<script>
(() => {
  const w = window.parent, d = w.document;
  if (w.__campusflowDrawerCleanup) w.__campusflowDrawerCleanup();
  let panel = null, active = false, previousOverflow = '';
  const selector = '[data-testid="stVerticalBlock"]';
  const buttons = () => [...(panel || d).querySelectorAll('button')];
  const closeButton = () => buttons().find(b => ['×','关闭个人设置'].includes(b.textContent.trim()));
  const visible = e => e.getClientRects().length && !e.disabled && e.getAttribute('tabindex') !== '-1';
  const focusables = () => [...panel.querySelectorAll('button,input,textarea,select,[tabindex="0"],summary')].filter(visible);
  const clearDialogAttrs = target => {
    if (!target || target.getAttribute('data-campusflow-dialog') !== 'true') return;
    target.removeAttribute('role'); target.removeAttribute('aria-modal');
    target.removeAttribute('aria-label'); target.removeAttribute('data-campusflow-dialog');
  };
  const activate = () => {
    // Change only the native status text node; preserve Stop and its handlers.
    for (const status of d.querySelectorAll('[data-testid="stStatusWidget"]')) {
      const walker = d.createTreeWalker(status, w.NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (/^(running|thinking|thikning)[.\u2026]*$/i.test(node.nodeValue.trim())
            && node.nodeValue.trim() !== 'THINKING') node.nodeValue = 'THINKING';
      }
    }
    const marker = d.querySelector('.cf-settings-marker');
    const found = marker && marker.closest(selector);
    if (found) {
      // Streamlit 1.31 date inputs otherwise expose only "Select a date."
      for (const widget of found.querySelectorAll('[data-testid="stDateInput"]')) {
        const input = widget.querySelector('input'), label = widget.querySelector('label');
        if (input && label) input.setAttribute('aria-label', label.textContent.trim());
      }
    }
    if (found && found !== panel) {
      clearDialogAttrs(panel);
      panel = found;
      panel.setAttribute('data-campusflow-dialog', 'true');
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-modal', 'true');
      panel.setAttribute('aria-label', '个人设置');
      if (!active) {
        active = true;
        const main = d.querySelector('section.main');
        if (main) { previousOverflow = main.style.overflowY; main.style.overflowY = 'hidden'; }
        const close = closeButton();
        if (close && !w.__campusflowDrawerWasOpen) close.focus({preventScroll:true});
        w.__campusflowDrawerWasOpen = true;
      }
    } else if (!found && active) {
      active = false;
      w.__campusflowDrawerWasOpen = false;
      clearDialogAttrs(panel);
      panel = null;
      const main = d.querySelector('section.main');
      if (main) main.style.overflowY = previousOverflow;
      const trigger = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '个人设置');
      if (trigger) trigger.focus({preventScroll:true});
      // The close rerun may replace that trigger after the overlay disappears.
      // One bounded follow-up restores a lost focus, but never steals focus
      // from another control the user has already chosen.
      w.setTimeout(() => {
        if (d.querySelector('.cf-settings-marker')) return;
        const current = d.activeElement;
        if (current && current !== d.body && current.isConnected && current !== trigger) return;
        const next = [...d.querySelectorAll('button')].find(b => b.textContent.trim() === '个人设置');
        if (next) next.focus({preventScroll:true});
      }, 160);
    }
    if (active && panel) {
      const close = closeButton();
      if (close) { close.setAttribute('aria-label', '关闭个人设置'); close.setAttribute('title', '关闭个人设置'); }
      const tabs = [...panel.querySelectorAll('[role="tab"]')];
      const target = tabs.find(t => t.textContent.trim() === w.__campusflowDrawerTab);
      // A save notice can remount only the tab subtree, keeping the outer
      // drawer element. Native tab clicks are client-only, not submissions.
      if (target && target.getAttribute('aria-selected') !== 'true') target.click();
    }
  };
  const rememberTab = event => {
    const tab = event.target.closest && event.target.closest('[role="tab"]');
    if (active && tab && panel.contains(tab)) w.__campusflowDrawerTab = tab.textContent.trim();
  };
  const keydown = event => {
    if (!active || !panel || !panel.isConnected) return;
    // Native select/calendar popups live in a portal outside the drawer.
    // Let them handle Escape and Tab before the drawer handles those keys.
    if (d.querySelector('[role="listbox"], [role="grid"] [role="gridcell"]')) return;
    if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key) && event.target.closest('[role="tab"]')) {
      const tabs = [...panel.querySelectorAll('[role="tab"]')], index = tabs.indexOf(event.target.closest('[role="tab"]'));
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : (index + (event.key === 'ArrowRight' ? 1 : tabs.length-1)) % tabs.length;
      if (tabs[next]) w.__campusflowDrawerTab = tabs[next].textContent.trim();
    }
    if (event.key === 'Escape') {
      const close = closeButton();
      if (close) { event.preventDefault(); close.click(); }
    }
    if (event.key === 'Tab') {
      const items = focusables(), first = items[0], last = items[items.length-1];
      if (!first) return;
      if (event.shiftKey && (d.activeElement === first || !panel.contains(d.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (d.activeElement === last || !panel.contains(d.activeElement))) { event.preventDefault(); first.focus(); }
    }
  };
  d.addEventListener('keydown', keydown);
  d.addEventListener('click', rememberTab, true);
  const observer = new w.MutationObserver(activate);
  observer.observe(d.body, {childList:true,subtree:true,characterData:true});
  activate();
  w.__campusflowDrawerCleanup = () => {
    observer.disconnect(); d.removeEventListener('keydown',keydown);
    d.removeEventListener('click',rememberTab,true);
    const main = d.querySelector('section.main');
    if (active && main) main.style.overflowY = previousOverflow;
    clearDialogAttrs(panel);
  };
})();
</script>
"""
