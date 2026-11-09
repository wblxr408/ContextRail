'use strict';
const byId = id => document.getElementById(id);
let library = [], selected = null, selectedId = null, detailGeneration = 0, sourceGeneration = 0, refreshGeneration = 0;
function say(id, text) { byId(id).textContent = text; byId(id).hidden = !text; }
async function read(path, options) {
  const response = await fetch(path + (options ? '?' + new URLSearchParams(options) : ''), {cache:'no-store'});
  const data = await response.json(); if (!response.ok) throw new Error(data.error || '资料暂不可读'); return data;
}
function element(tag, text, className) { const node = document.createElement(tag); node.textContent = text; if (className) node.className = className; return node; }
function choices() {
  const text = byId('filter').value.trim().toLocaleLowerCase();
  const items = library.filter(item => [item.task_id, item.snapshot_id, item.goal].join(' ').toLocaleLowerCase().includes(text));
  byId('bundles').replaceChildren(...items.map(item => {
    const button = element('button', '', 'bundle-choice'); button.type = 'button'; button.setAttribute('aria-pressed', String(item.id === selectedId));
    button.append(element('strong', item.task_id), element('small', item.snapshot_id), element('p', item.goal || '此版本未记录有效目标'));
    button.onclick = () => loadBundle(item.id); return button;
  }));
  if (!items.length) byId('bundles').append(element('p', library.length ? '没有匹配的交接版本。' : '本项目还没有保存 Bridge 交接包。', 'panel-caption'));
}
function requirements() {
  const target = byId('requirements'); target.replaceChildren(); if (!selected) return;
  const kinds = {goal:'目标',constraint:'约束',deliverable:'交付物'}, progress = {pending:'待处理',in_progress:'进行中',done:'记录为完成'};
  for (const row of selected.requirements.filter(row => byId('show-history').checked || row.status === 'active')) {
    const card = element('article', '', 'requirement' + (row.status === 'active' ? '' : ' superseded'));
    const heading = element('div', '', 'requirement-header'); heading.append(element('strong', row.id),element('span', 'rev ' + row.revision),element('span', kinds[row.kind] || row.kind),element('span', row.status === 'active' ? progress[row.progress] || '状态未知' : '已替代 → ' + (row.superseded_by || '未标注')));
    card.append(heading, element('h3', row.statement));
    if (row.acceptance) card.append(element('p', '验收约定：' + row.acceptance));
    const quote = element('p', row.source.quote || '来源未记录摘录', 'quote'); card.append(quote);
    if (row.source.path) { const source = element('button', '查看来源 · ' + row.source.path); source.onclick = () => loadSource(row.source.path); card.append(source); }
    target.append(card);
  }
  if (!target.childElementCount) target.append(element('p','此版本没有有效要求。','panel-caption'));
}
async function loadSource(name) {
  if (!selected) return; const generation = ++sourceGeneration, bundle = selectedId;
  say('error',''); byId('source-title').textContent = name; byId('source-content').textContent = '正在读取原文…'; byId('source-meta').textContent = '';
  try {
    const value = await read('/api/bridge/source',{bundle,file:name});
    if (generation !== sourceGeneration || bundle !== selectedId) return;
    byId('source-content').textContent = value.text;
    byId('source-meta').textContent = value.truncated ? '仅展示前 64 KiB 的脱敏原文；完整材料位于交接包路径。' : '按本次读取展示脱敏原文；历史资料需结合当前要求使用。';
    for (const button of byId('files').children) button.setAttribute('aria-pressed',String(button.textContent === name));
  } catch (error) { if (generation === sourceGeneration) { byId('source-content').textContent = '此材料当前不可读。'; say('error',String(error)); } }
}
async function loadBundle(id) {
  const generation = ++detailGeneration; ++sourceGeneration; selectedId = id; selected = null; choices();
  byId('copy-resume').disabled = byId('copy-path').disabled = true;
  byId('goal').textContent = '正在读取选中的交接版本…'; byId('requirements').replaceChildren(); byId('files').replaceChildren(); byId('copy-status').textContent = ''; byId('source-content').textContent = '选择材料查看脱敏原文。'; byId('source-meta').textContent = ''; say('error','');
  try {
    const data = await read('/api/bridge/bundle',{bundle:id}); if (generation !== detailGeneration) return; selected = data;
    byId('detail-title').textContent = data.task_id; byId('version-label').textContent = data.snapshot_id;
    byId('goal').textContent = data.goals.join('\n') || '此版本没有有效目标。';
    byId('selection-meta').textContent = `${data.active_count} 项有效要求 · ${data.superseded_count} 项历史要求 · ${data.bundle_path}`;
    byId('copy-resume').disabled = byId('copy-path').disabled = false; requirements();
    byId('files').replaceChildren(...data.files.map(name => { const button = element('button',name); button.onclick = () => loadSource(name); return button; }));
    const initial = ['START.md','STATE.md','state.md'].find(name => data.files.includes(name)); if (initial) await loadSource(initial);
  } catch (error) { if (generation === detailGeneration) { byId('goal').textContent = '所选版本当前不可读，请刷新档案。'; say('error',String(error)); } }
}
async function refresh() {
  const generation = ++refreshGeneration; byId('refresh').disabled = true; say('error','');
  try {
    const data = await read('/api/bridge/bundles'); if (generation !== refreshGeneration) return; library = data.items;
    byId('workspace').textContent = data.workspace + ' · 本项目交接包'; byId('bundle-count').textContent = library.length + ' VERSIONS'; choices();
    say('notice',[data.errors.length ? `${data.errors.length} 个交接账本暂不可读，其他版本仍可查看。` : '',data.truncated ? '目录已达到查看上限，当前列表有省略。' : ''].filter(Boolean).join(' '));
    if (selectedId && library.some(item => item.id === selectedId)) await loadBundle(selectedId);
    else if (!selectedId && library.length) await loadBundle(library[0].id);
    else if (selectedId) { ++detailGeneration; ++sourceGeneration; selected = null; selectedId = null; byId('goal').textContent = '此前选择的版本已不可读。请重新选择。'; byId('requirements').replaceChildren(); byId('files').replaceChildren(); byId('source-content').textContent = ''; byId('copy-resume').disabled = byId('copy-path').disabled = true; }
  } catch (error) { say('error',String(error) + '。此前显示内容来自上次读取。'); }
  finally { if (generation === refreshGeneration) byId('refresh').disabled = false; }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); byId('copy-status').textContent = '已复制到剪贴板。'; }
  catch { byId('copy-status').textContent = '浏览器未允许复制，请手动复制：' + text; }
}
byId('copy-resume').onclick = () => selected && copy('$context-bridge resume ' + JSON.stringify(selected.bundle_path));
byId('copy-path').onclick = () => selected && copy(selected.bundle_path);
byId('show-history').onchange = requirements; byId('filter').oninput = choices; byId('refresh').onclick = refresh;
const keepAlive = setInterval(() => { if (document.visibilityState === 'visible') read('/api/bridge/bundles').catch(() => {}); },60000);
window.addEventListener('pagehide',() => clearInterval(keepAlive));
refresh();
