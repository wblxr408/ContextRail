import React from 'react';
import { ThemeToggle } from './ThemeToggle';
import { ThemePreference } from '../hooks/useTheme';
interface HeaderProps {
  projects: string[]; currentFilter: string; onFilterChange: (filter: string) => void;
  isProcessing: boolean; queueDepth: number; themePreference: ThemePreference;
  onThemeChange: (theme: ThemePreference) => void; onContextPreviewToggle: () => void;
  onShowHelp?: () => void; showTeamBrainLink?: boolean;
}
export function Header({ projects, currentFilter, onFilterChange, themePreference, onThemeChange, onContextPreviewToggle }: HeaderProps) {
  return <div className="header"><div className="header-main"><a href="/" className="local-work-link">My Work</a><h1><img src="/claude-mem-logomark.webp" alt="" className="logomark" /><span className="logo-text">ContextRail · Memory</span></h1></div><div className="status"><a className="local-work-link" href="/rail">状态与文件历史</a><label>来源 <select aria-label="筛选记忆来源" value={currentFilter} onChange={event => onFilterChange(event.target.value)}><option value="">全部来源</option>{projects.map(project => <option key={project} value={project}>{project}</option>)}</select></label><button className="local-work-link" onClick={onContextPreviewToggle}>接线设置</button><ThemeToggle preference={themePreference} onThemeChange={onThemeChange} /></div></div>;
}
