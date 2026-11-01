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
  target.append(row('原生采集', `${data.capture?.raw_events ?? '未知'} 条已捕获事件；${data.capture?.status || '未知'}`));
  target.append(row('记忆处理队列', `已保存 ${data.learning?.saved_jobs ?? '未知'} · 等待处理 ${data.learning?.pending_jobs ?? '未知'}；工作区统计`));
  target.append(row('压缩后补注入', data.native_assistance?.compact?.last_returned ? '已记录返回回执；模型是否消费以原记录为准。' : '尚无返回回执；展开原生辅助记录查看。'));
  const causes = { quota_deferred: '配额等待：等记录中的额度窗口恢复；重启不能恢复配额。', credential_rejected: '凭证被拒：请在现有记忆服务中核对凭证。', setup_required: '原服务尚需完成配置。', index_unavailable: '索引暂不可用；原文采集成功不能证明已完成索引。', resource_deferred: '资源退避：等待原服务的退避窗口结束。', queue_deferred: '队列延后：当前证据不能确认具体原因。', awaiting_index: '等待索引：处理结果尚未确认可检索。', failed_jobs: '有失败任务：需检查原队列记录，界面不会自动重放。', memory_state_unavailable: '原记忆状态库不可读；请核对现有绑定和访问权限。', queue_control_unavailable: '原队列缺少可读控制详情，处理原因未知。', classifier_unavailable: '上游故障分类器不可用；需要支持原生 TypeScript 的 Node。' };
  const grouped = new Map();
  for (const item of data.diagnostics || []) {
    const previous = grouped.get(item.code);
    grouped.set(item.code, previous ? { ...previous, count: previous.count + 1, next_retry: Math.max(previous.next_retry || 0, item.next_retry || 0) } : { ...item, count: 1 });
  }
  for (const item of grouped.values()) {
    const due = item.next_retry ? (item.next_retry * 1000 < Date.now() ? '记录中的重试时刻已过，是否恢复未知。' : '记录的下次重试：' + new Date(item.next_retry * 1000).toLocaleString()) : '是否恢复以原服务后续记录为准。';
    target.append(row(causes[item.code] || item.code, `${item.count} 条同类记录 · ${due}`));
  }
  for (const [key, value] of Object.entries(data)) {
    if (['schema', 'model_calls', 'scope', 'trust', 'display_truncated'].includes(key)) continue;
    const entry = document.createElement('details'), summary = document.createElement('summary'), body = document.createElement('pre');
    const names = { task: '任务与要求', capture: '采集状态', learning: '记忆队列', sources: '资料来源', capabilities: '适配能力', errors: '已记录故障', source_errors: '来源故障', native: '原生客户端', explanation: '解释', recovery: '恢复建议', diagnostics: '原故障记录', effective_config: '实际绑定配置', injection: '注入回执', native_assistance: '压缩后与文件辅助记录', requirements: '要求来源新鲜度', task_index: '任务索引', process_liveness: '进程状态证据边界', display_truncated: '展示是否截断' };
    summary.textContent = names[key] || key; body.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); entry.append(summary, body); target.append(entry);
  }
}
async function products() {
  const data = await api('/api/rail/products'); pre('product-data', data);
  $('product-list').replaceChildren(...data.items.map((item, index) => row(item.title, `${item.kind} · ${item.availability.state}\n${item.registered_path || item.open_url || ''}`, item.open_path ? link('打开文件 ↗', '/api/rail/product?ref=' + encodeURIComponent(JSON.stringify(item.ref))) : item.open_url ? link('打开地址 ↗', item.open_url) : document.createTextNode('原文件当前不可读'))));
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
