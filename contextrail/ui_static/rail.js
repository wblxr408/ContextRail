'use strict';
const $ = id => document.getElementById(id);
let config = null;
function message(id, text) { $(id).textContent = text; $(id).hidden = !text; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function failed(error) { message('error', String(error) + '。此前显示内容保留，仅代表上次成功读取；请刷新后再操作。'); }
function pre(target, data) { $(target).textContent = JSON.stringify(data, null, 2); }
function row(title, detail, action) {
  const div = document.createElement('div'); div.className = 'row';
  const copy = document.createElement('div'), strong = document.createElement('strong'), p = document.createElement('p');
  strong.textContent = title; p.textContent = detail; copy.append(strong, p); div.append(copy); if (action) div.append(action); return div;
}
function button(label, action) {
  const b = document.createElement('button'); b.textContent = label;
  b.onclick = async () => { b.disabled = true; message('error', ''); try { await action(); } catch (error) { failed(error); } finally { b.disabled = false; } }; return b;
}
function link(label, url) {
  const parsed = new URL(url, location.origin);
  if (!['http:', 'https:'].includes(parsed.protocol)) return document.createTextNode('地址不可打开');
  const a = document.createElement('a'); a.textContent = label; a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
}
async function settings() {
  config = await api('/api/rail/config');
  $('scope').textContent = config.workspace + ' · ' + config.task;
  $('settings-source').textContent = '保存到 ' + config.source + '；记忆来源 ' + config.memory.root;
  $('clients').replaceChildren(...config.clients.map(client => row(client.client, client.enabled ? '当前项目已启用' : '当前项目已暂停', button((client.enabled ? '暂停 ' : '启用 ') + client.client, async () => {
    await api('/api/rail/config', { client: client.client, enabled: !client.enabled, expectedRevision: config.revision });
    await settings(); message('notice', client.client + ' 已保存，下次原生 Hook 生效。');
  }))));
}
async function status() {
  const data = await api('/api/rail/status'); pre('status-data', data);
  const target = $('status-summary'); target.replaceChildren();
  $('metric-capture').textContent = data.capture?.raw_events ?? '—';
  $('metric-pending').textContent = data.learning?.pending_jobs ?? '—';
  $('capture-count').textContent = data.capture?.raw_events ?? '—';
  $('capture-content').replaceChildren(
    row('待处理', String(data.learning?.pending_jobs ?? '未知')),
    row('已保存', String(data.learning?.saved_jobs ?? '未知')),
    row('失败', String(data.learning?.states?.failed ?? '未记录'))
  );
  const compact = data.native_assistance?.compact;
  const returned = compact?.last_returned;
  const sourceCurrent = data.requirements?.external_source_current;
  $('continuity-content').replaceChildren(
    row('压缩后补注入', returned ? '已记录返回回执；模型消费状态以原记录为准。' : '暂无返回回执，来源状态保持未知。'),
    row('要求来源', sourceCurrent === true ? '所选快照来源可读' : sourceCurrent === false ? '快照来源有缺口，请核对原记录' : '尚未确认来源新鲜度'),
    row('确切快照', data.requirements?.snapshot || '尚未绑定快照')
  );
  const causes = { quota_deferred: '配额等待', credential_rejected: '凭证被拒', setup_required: '原服务待配置', index_unavailable: '索引暂不可用', resource_deferred: '资源退避', queue_deferred: '队列延后 · 原因未确认', awaiting_index: '等待索引', failed_jobs: '原队列有失败任务', memory_state_unavailable: '原记忆状态库不可读', queue_control_unavailable: '队列控制详情不可读', classifier_unavailable: '故障分类器不可用' };
  const guidance = { quota_deferred: '等待记录中的额度窗口，重启不能恢复配额。', credential_rejected: '请在现有记忆服务中核对凭证。', setup_required: '完成原服务配置后继续原重试队列。', index_unavailable: '检查原索引队列，采集成功不表示已索引。', resource_deferred: '等待原退避窗口结束，再核对后续记录。', queue_deferred: '当前记录不足以确认具体原因。', awaiting_index: '处理结果尚未确认可检索。', failed_jobs: '请检查原队列记录，是否恢复以新记录为准。' };
  const grouped = new Map();
  for (const item of data.diagnostics || []) {
    const previous = grouped.get(item.code);
    grouped.set(item.code, previous ? { ...previous, count: previous.count + 1, next_retry: Math.max(previous.next_retry || 0, item.next_retry || 0) } : { ...item, count: 1 });
  }
  $('fault-count').textContent = grouped.size ? grouped.size + ' 类记录' : '暂无故障记录';
  for (const item of grouped.values()) {
    const due = item.next_retry ? (item.next_retry * 1000 < Date.now() ? '记录重试时刻已过，恢复状态未知。' : '记录重试时间 ' + new Date(item.next_retry * 1000).toLocaleTimeString()) : guidance[item.code] || item.action || '请核对原服务后续记录。';
    target.append(row(causes[item.code] || item.code, `${item.count} 条同类记录 · ${due}`));
  }
  if (!grouped.size) target.append(row('尚无故障分类记录', '仅表示本次来源未返回分类结果。'));
  $('last-read').textContent = '本次读取 ' + new Date().toLocaleTimeString() + ' · LOCAL';
}
async function products() {
  const data = await api('/api/rail/products'); pre('product-data', data);
  $('metric-products').textContent = data.items.length; $('product-count').textContent = data.items.length + ' ITEMS';
  $('product-list').replaceChildren(...data.items.map((item, index) => row(item.title, `${({metadata_matches:'登记元数据吻合',changed:'当前文件已改变',missing:'原文件缺失',not_checked_remote:'原登记地址'})[item.availability.state] || item.availability.state} · ${item.registered_path || item.open_url || ''}`, item.open_path ? link('打开文件 ↗', '/api/rail/product?ref=' + encodeURIComponent(JSON.stringify(item.ref))) : item.open_url ? link('打开地址 ↗', item.open_url) : document.createTextNode('原文件当前不可读'))));
  if (!data.items.length) $('product-list').textContent = '当前任务尚未登记交付物。';
}
$('history-form').onsubmit = async event => {
  event.preventDefault(); message('error', ''); const target = $('history-results');
  try {
    const data = await api('/api/rail/history?file=' + encodeURIComponent($('file').value)); target.replaceChildren();
    const explanation = document.createElement('p'); explanation.className = 'muted'; explanation.textContent = `${data.relative_path} · ${data.entries.length} 条已捕获记录${data.truncated ? '（有截断）' : ''}；历史资料不能替代当前要求。`; target.append(explanation);
    for (const entry of data.entries) {
      const ref = entry.ref;
      target.append(row(entry.title || entry.kind || entry.id || '历史记录', entry.summary || entry.text || entry.preview || entry.at || '', ref ? button('回读原文', async () => {
        const result = await api('/api/rail/history-detail?ref=' + encodeURIComponent(JSON.stringify(ref)));
        const detail = document.createElement('details'); detail.open = true;
        const summary = document.createElement('summary'), body = document.createElement('pre'); summary.textContent = '确切来源原文'; body.textContent = JSON.stringify(result, null, 2); detail.append(summary, body); target.append(detail);
      }) : null));
    }
    if (!data.entries.length) { const p = document.createElement('p'); p.textContent = '已绑定来源没有该文件的捕获记录。'; target.append(p); }
    const detail = document.createElement('details'), summary = document.createElement('summary'), body = document.createElement('pre'); summary.textContent = '覆盖范围与来源故障'; body.textContent = JSON.stringify({ coverage: data.coverage, source_errors: data.source_errors }, null, 2); detail.append(summary, body); target.append(detail);
  } catch (error) { failed(error); }
};
async function refresh() {
  message('error', '');
  const results = await Promise.allSettled([settings(), status(), products()]);
  const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
  if (errors.length) failed(errors.join('\n'));
}
$('refresh').onclick = refresh;
$('stop').onclick = async () => { pageConnection.close(); try { await api('/api/rail/stop', {}); message('notice', '界面服务已关闭。下次使用项目中的 Open-ContextRailUI.ps1 启动。'); } catch (error) { failed(error); } };
const pageConnection = new EventSource('/stream');
pageConnection.onmessage = event => {
  try {
    const data = JSON.parse(event.data);
    if (data.source_errors?.length) failed(data.source_errors.map(item => item.source + ': ' + item.reason).join('；'));
  } catch { /* Keep the last explicitly read view through malformed stream data. */ }
};
pageConnection.onerror = () => failed('界面连接已断开；自动重连中');
window.addEventListener('pagehide', () => pageConnection.close());
refresh();
