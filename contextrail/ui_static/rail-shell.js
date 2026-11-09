// One shared navigation shell for the workbench and both upstream React apps.
class ContextRailShell extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const active = this.getAttribute('active');
    const bridge = active === 'bridge';
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <link rel="stylesheet" href="/context-theme.css">
      <style>:host{display:block;height:var(--rail-shell-height);flex:0 0 var(--rail-shell-height);font-family:var(--font-sans)}</style>
      <header class="rail-topbar">
        <a href="${bridge ? '/bridge' : '/rail'}" class="rail-brand"><span class="rail-logo-box"><img src="${bridge ? '/bridge-icon.svg' : '/contextrail-logo.png'}" alt="" width="60" height="60"></span><span>${bridge ? 'Context Bridge' : 'ContextRail'}<small>${bridge ? 'TASK CONTINUITY WORKSPACE' : 'PERSONAL CONTEXT SYSTEM'}</small></span></a>
        <nav class="rail-topnav" aria-label="主导航">${bridge ? '<a href="/bridge" data-page="bridge">交接工作台</a><a href="#library-title">交接版本</a><a href="#source-title">来源原文</a>' : '<a href="/rail" data-page="rail">工作台</a><a href="/" data-page="work">My Work</a><a href="/viewer" data-page="memory">记忆档案</a>'}</nav>
        <div class="rail-shell-right"><slot name="tools"></slot><span class="rail-local"><i></i><span class="local-label">本机工作区</span><span class="rail-avatar">${bridge ? 'CB' : 'CR'}</span></span></div>
      </header>`;
    for (const link of root.querySelectorAll('[data-page]')) {
      if (link.dataset.page === active) link.setAttribute('aria-current', 'page');
    }
  }
}
if (!customElements.get('context-rail-shell')) customElements.define('context-rail-shell', ContextRailShell);
