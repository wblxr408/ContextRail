import React from 'react';
export function getStoredWelcomeDismissed() { return true; }
export function setStoredWelcomeDismissed(_dismissed: boolean) { /* No upstream onboarding in the local adapter. */ }
export function WelcomeCard({ onDismiss }: { onDismiss: () => void }) { return <button onClick={onDismiss}>打开本地记忆浏览</button>; }
