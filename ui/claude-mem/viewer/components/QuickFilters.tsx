import React, { useEffect, useRef } from 'react';
import { CategoryFilter } from './CategoryFilter';
import type { MemoryFilters } from '../utils/quick-filter';

interface Props {
  value: MemoryFilters;
  onChange: (value: MemoryFilters) => void;
  onClear: () => void;
  counts: Record<string, number>;
  matched: number;
  loaded: number;
  hasMore: boolean;
  sessions: boolean;
}
const SOURCES = [['', '全部来源'], ['basic-memory', 'Basic Memory'], ['codex', 'Codex'], ['claude', 'Claude']];
const CATEGORY_LABELS = { prompt: '请求', summary: '摘要', discovery: '发现', decision: '决策', bugfix: '修复', feature: '功能', refactor: '重构', change: '变更', security_alert: '安全警报', security_note: '安全记录', other: '其他' };
export function QuickFilters({ value, onChange, onClear, counts, matched, loaded, hasMore, sessions }: Props) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === '/' && !event.ctrlKey && !event.altKey && !event.metaKey
        && !target.closest('input,textarea,select,[contenteditable="true"]')) {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);
  return <section className="rail-quick-filters" aria-label="快速筛选档案">
    <div className="rail-filter-search">
      <span aria-hidden="true">⌕</span>
      <input ref={input} type="search" aria-label="搜索记忆档案" value={value.query}
        placeholder={sessions ? '搜会话标题、项目或会话 ID…' : '搜标题、正文预览或文件路径…'}
        onChange={event => onChange({ ...value, query: event.target.value })}
        onKeyDown={event => { if (event.key === 'Escape') onChange({ ...value, query: '' }); }} />
      <kbd title="按斜杠聚焦搜索，Esc 清除关键词">/</kbd>
      <button className="rail-filter-clear" onClick={onClear}>清除筛选</button>
    </div>
    <div className="rail-filter-options">
      <div className="rail-filter-sources" role="group" aria-label="来源快捷筛选">
        {SOURCES.map(([source, label]) => <button key={source || 'all'} aria-pressed={value.source === source}
          onClick={() => onChange({ ...value, source })}>{label}</button>)}
      </div>
      <label className="rail-filter-time">时间 <select aria-label="筛选档案时间" value={value.days}
        onChange={event => onChange({ ...value, days: Number(event.target.value) })}>
        <option value="0">全部时间</option><option value="1">最近24小时</option>
        <option value="7">最近7天</option><option value="30">最近30天</option>
      </select></label>
      <span className="rail-filter-result" role="status" aria-live="polite">当前匹配 {matched} / 已载入 {loaded}{hasMore ? ' · 还有未加载记录' : ''}</span>
    </div>
    {!sessions && <CategoryFilter labels={CATEGORY_LABELS} categoryCounts={counts} activeCategories={new Set(value.categories)}
      onToggle={category => onChange({ ...value, categories: value.categories.includes(category) ? value.categories.filter(c => c !== category) : [...value.categories, category] })}
      filteredCount={matched} totalCount={loaded} totalIsPartial={hasMore} />}
    <p className="rail-filter-scope">{sessions ? '关键词匹配已载入的会话标题、项目和 ID；时间按会话开始时间。' : '关键词与分类匹配已载入的正文预览；时间按来源记录时间，main 笔记按文件修改时间。'}未载入内容可在列表下方继续载入，不代表全库检索。</p>
  </section>;
}
