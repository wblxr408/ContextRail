import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { filterMemories, filterSessions, EMPTY_MEMORY_FILTERS } = await import(pathToFileURL(process.env.CONTEXTRAIL_FILTER_MODULE));
const now = Date.parse('2026-11-02T04:00:00Z');
const day = 86400000;
const record = (id, extra = {}) => ({ id, itemType: 'observation', platform_source: 'basic-memory', type: 'discovery', title: 'ContextRail', subtitle: '', narrative: '', created_at_epoch: now, ...extra });
const rows = [
  record(1, { title: '检索失败的原因', narrative: 'FTS5 path D:/ContextRail/README.md', platform_source: 'codex', type: 'bugfix' }),
  record(2, { title: '检索失败的原因', platform_source: 'claude', type: 'bugfix' }),
  record(3, { title: '检索失败的原因', narrative: 'FTS5', platform_source: 'codex', type: 'bugfix', created_at_epoch: now - 8 * day }),
  record(4, { title: '部署决定', type: 'decision' }),
];
test('keyword, source, date and category intersect without changing original order', () => {
  const result = filterMemories(rows, { query: '检索 ＦＴＳ５', source: 'codex', days: 7, categories: ['bugfix'] }, now);
  assert.deepEqual(result.map(x => x.id), [1]);
});
test('file paths and body previews are searchable without changing source records', () => {
  assert.deepEqual(filterMemories(rows, { ...EMPTY_MEMORY_FILTERS, query: 'd:\\contextrail\\readme.md' }, now).map(x => x.id), [1]);
  assert.equal(rows[0].narrative, 'FTS5 path D:/ContextRail/README.md');
});
test('empty query clears keyword filtering; no match is empty', () => {
  assert.deepEqual(filterMemories(rows, EMPTY_MEMORY_FILTERS, now).map(x => x.id), [1, 2, 3, 4]);
  assert.deepEqual(filterMemories(rows, { ...EMPTY_MEMORY_FILTERS, query: 'not-present-in-any-record' }, now), []);
});
test('date cutoff includes the boundary and excludes unknown dates', () => {
  const dated = [record(5, { created_at_epoch: now-day }), record(6, { created_at_epoch: now-day-1 }), record(7, { created_at_epoch: 0 })];
  assert.deepEqual(filterMemories(dated, { ...EMPTY_MEMORY_FILTERS, days: 1 }, now).map(x => x.id), [5]);
});
test('category selection is OR inside the category group and AND with keyword', () => {
  assert.deepEqual(filterMemories(rows, { ...EMPTY_MEMORY_FILTERS, categories: ['decision', 'bugfix'], query: '部署' }, now).map(x => x.id), [4]);
});
test('sessions match title or exact session identity with source and time limits', () => {
  const sessions = [
    { content_session_id: 'session-rail-12', project: 'ContextRail', platform_source: 'codex', custom_title: '恢复检查点', started_at_epoch: now, item_count: 4 },
    { content_session_id: 'session-rail-13', project: 'ContextRail', platform_source: 'claude', custom_title: '恢复检查点', started_at_epoch: now, item_count: 3 },
  ];
  assert.deepEqual(filterSessions(sessions, { ...EMPTY_MEMORY_FILTERS, query: '恢复 rail-12', source: 'codex', days: 7 }, now).map(x => x.content_session_id), ['session-rail-12']);
});
