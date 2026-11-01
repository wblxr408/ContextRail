let histories = [];
let allowed = [];
export function bindFileContext(value, paths) { histories=value; allowed=paths; }
export async function lookupFileObservationHistory(input,path) { return histories.find(h=>h.filePath===path) || null; }
export function claimFileContextInjection(session,path,epoch) { return allowed.includes(path); }
