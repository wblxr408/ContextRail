import React from 'react';
import { useDialogFocus } from '../hooks/useDialogFocus';
import type { Settings } from '../types';
interface ContextSettingsModalProps {
  isOpen: boolean; onClose: () => void; settings: Settings; isLoaded: boolean;
  loadError: string | null; onRetryLoad: () => void; onSave: (settings: Settings) => void;
  isSaving: boolean; saveStatus: string;
}
export function saveStatusClass(status: string) { return status.includes('✗') ? 'error' : status.includes('✓') ? 'success' : ''; }
export function ContextSettingsModal({ isOpen, onClose }: ContextSettingsModalProps) {
  const ref = useDialogFocus(isOpen);
  if (!isOpen) return null;
  return <div className="modal-overlay" onClick={onClose}><div className="modal-content" role="dialog" aria-modal="true" aria-label="本地项目接线" ref={ref} tabIndex={-1} onClick={event => event.stopPropagation()} style={{ padding: 28, maxWidth: 560 }}><h2>本地项目接线</h2><p>这里读取原 Basic Memory main 与已绑定原生会话。账号、模型和全局 Hook 由现有客户端管理。</p><p><a href="/rail#settings">管理当前项目开关</a> · <a href="/rail">故障解释与文件历史</a></p><button onClick={onClose}>关闭</button></div></div>;
}
