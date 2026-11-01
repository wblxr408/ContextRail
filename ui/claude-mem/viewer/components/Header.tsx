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
  return <div className="header rail-memory-header"><div className="header-main"><a href="/rail" className="rail-brand"><span className="rail-logo-box"><img src="/contextrail-logo.png" alt="" /></span><span>ContextRail<small>MEMORY ARCHIVE</small></span></a><nav className="rail-topnav" aria-label="主导航"><a href="/rail">工作台</a><a href="/">My Work</a><a href="/viewer" aria-current="page">记忆档案</a></nav></div><div className="status"><label>来源 <select aria-label="筛选记忆来源" value={currentFilter} onChange={event => onFilterChange(event.target.value)}><option value="">全部来源</option>{projects.map(project => <option key={project} value={project}>{project}</option>)}</select></label><button className="local-work-link" onClick={onContextPreviewToggle}>接线设置</button><ThemeToggle preference={themePreference} onThemeChange={onThemeChange} /></div></div>;
}
