// One shared navigation shell for the workbench and both upstream React apps.
class ContextRailShell extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <link rel="stylesheet" href="/context-theme.css">
      <style>
        :host{display:block;flex:0 0 var(--rail-shell-height);height:var(--rail-shell-height);font-family:var(--font-sans);font-size:14px;font-weight:400;line-height:1.55}
        .rail-topbar{position:fixed;inset:0 0 auto;z-index:50;display:grid;grid-template-columns:clamp(210px,24vw,360px) auto minmax(0,1fr);gap:22px;align-items:center;height:var(--rail-shell-height);min-height:0;max-width:none;padding:0 32px;background:var(--rail-canvas);margin:0}
        .rail-brand{height:100%}.rail-topnav{height:100%;padding-top:12px}.rail-topnav a{font-size:12px;flex:0 0 88px;width:88px;justify-content:center;padding:10px 12px 11px}.rail-topnav a[aria-current="page"]{padding-top:14px}
        .rail-shell-right{display:flex;justify-content:flex-end;align-items:center;gap:16px;min-width:0}.rail-local{margin:0}.rail-brand small{font-size:8px}
        @media(max-width:1100px){.rail-topbar{grid-template-columns:210px auto minmax(0,1fr);gap:14px;padding:0 20px}.rail-topnav a{padding-left:12px;padding-right:12px}.rail-shell-right{gap:8px}.rail-local .local-label{display:none}}
        @media(max-width:760px){.rail-topbar{grid-template-columns:minmax(0,1fr) auto;grid-template-rows:48px 36px 38px;padding:0 14px;gap:0 8px}.rail-brand{grid-column:1;grid-row:1}.rail-brand small{font-size:6px}.rail-local{position:absolute;right:14px;top:12px}.rail-local .local-label{display:none}.rail-avatar{width:25px;height:25px}.rail-shell-right{grid-column:1/-1;grid-row:2;justify-content:flex-start;padding-right:0}.rail-topnav{grid-column:1/-1;grid-row:3;order:initial;width:100%;padding-top:0;height:38px}.rail-topnav a{font-size:11px;flex:1;justify-content:center;padding:8px 10px!important}.rail-topnav a[aria-current="page"]{padding-top:10px!important}}
      </style>
      <header class="rail-topbar">
        <a href="/rail" class="rail-brand"><span class="rail-logo-box"><img src="/contextrail-logo.png" alt="" width="60" height="60"></span><span>ContextRail<small>PERSONAL CONTEXT SYSTEM</small></span></a>
        <nav class="rail-topnav" aria-label="主导航"><a href="/rail" data-page="rail">工作台</a><a href="/" data-page="work">My Work</a><a href="/viewer" data-page="memory">记忆档案</a></nav>
        <div class="rail-shell-right"><slot name="tools"></slot><span class="rail-local"><i></i><span class="local-label">本机工作区</span><span class="rail-avatar">CR</span></span></div>
      </header>`;
    const active = this.getAttribute('active');
    for (const link of root.querySelectorAll('[data-page]')) {
      if (link.dataset.page === active) link.setAttribute('aria-current', 'page');
    }
  }
}
if (!customElements.get('context-rail-shell')) customElements.define('context-rail-shell', ContextRailShell);
