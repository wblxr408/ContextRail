import type { FeedItem, SessionCatalogEntry } from '../types';
import { categoryOf } from './category.js';

export interface MemoryFilters {
  query: string;
  source: string;
  days: number;
  categories: string[];
}
export const EMPTY_MEMORY_FILTERS: MemoryFilters = { query: '', source: '', days: 0, categories: [] };
function normalized(text: string): string {
  return text.normalize('NFKC').toLocaleLowerCase().replaceAll('\\', '/');
}
function matches(text: string, terms: string[]): boolean {
  const haystack = normalized(text);
  return terms.every(term => haystack.includes(term));
}
export function filterMemories(items: FeedItem[], filters: MemoryFilters, now: number): FeedItem[] {
  const terms = normalized(filters.query).trim().split(/\s+/u).filter(Boolean);
  const cutoff = filters.days ? now - filters.days * 86400000 : 0;
  return items.filter(item => {
    if (filters.source && item.platform_source !== filters.source) return false;
    if (cutoff && (!item.created_at_epoch || item.created_at_epoch < cutoff)) return false;
    if (filters.categories.length && !filters.categories.includes(categoryOf(item))) return false;
    const text = item.itemType === 'observation'
      ? [item.title, item.subtitle, item.narrative, item.text, item.facts, item.concepts, item.files_read, item.files_modified]
      : item.itemType === 'summary'
        ? [item.request, item.investigated, item.learned, item.completed, item.next_steps, item.notes]
        : [item.prompt_text];
    return matches([item.project, item.platform_source, ...text].filter(Boolean).join(' '), terms);
  });
}
export function filterSessions(items: SessionCatalogEntry[], filters: MemoryFilters, now: number): SessionCatalogEntry[] {
  const terms = normalized(filters.query).trim().split(/\s+/u).filter(Boolean);
  const cutoff = filters.days ? now - filters.days * 86400000 : 0;
  return items.filter(item => (!filters.source || item.platform_source === filters.source)
    && (!cutoff || !!item.started_at_epoch && item.started_at_epoch >= cutoff)
    && matches([item.custom_title, item.content_session_id, item.project, item.platform_source].filter(Boolean).join(' '), terms));
}
