import React from 'react';
import { createPortal } from 'react-dom';
import { ThemeToggle } from './ThemeToggle';
import { ThemePreference } from '../hooks/useTheme';
interface HeaderProps {
  projects: string[]; currentFilter: string; onFilterChange: (filter: string) => void;
  isProcessing: boolean; queueDepth: number; themePreference: ThemePreference;
  onThemeChange: (theme: ThemePreference) => void; onContextPreviewToggle: () => void;
  onShowHelp?: () => void; showTeamBrainLink?: boolean;
}
export function Header({ projects, currentFilter, onFilterChange, themePreference, onThemeChange, onContextPreviewToggle }: HeaderProps) {
  return createPortal(
    <div slot="tools" className="rail-shell-tools">
      <label>范围 <select aria-label="筛选记忆来源" value={currentFilter} onChange={event => onFilterChange(event.target.value)}><option value="">全部来源</option>{projects.map(project => <option key={project} value={project}>{project}</option>)}</select></label>
      <button className="local-work-link" onClick={onContextPreviewToggle}>接线设置</button>
      <ThemeToggle preference={themePreference} onThemeChange={onThemeChange} />
    </div>, document.querySelector('context-rail-shell')!);
}
