export function resolveDataDir() { if (!process.env.CONTEXTRAIL_MARKER_DIR) throw new Error('Marker root unbound'); return process.env.CONTEXTRAIL_MARKER_DIR; }
