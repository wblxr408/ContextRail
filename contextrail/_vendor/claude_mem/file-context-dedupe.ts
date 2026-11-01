// Original SQL; original R SQLite connection owns persistence.
export const GATE_TABLE_DDL = `
  CREATE TABLE IF NOT EXISTS file_context_injections (
    session_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    observation_epoch INTEGER NOT NULL,
    injected_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (session_id, file_path)
  )
`;

// Claiming and recording are ONE statement, so two concurrent Reads of the same
// file in the same session cannot both pass the gate: SQLite serializes the
// writers, the first inserts and gets its row back, the second conflicts and is
// filtered out by the WHERE, which returns no row. `RETURNING` is what reports
// the outcome — bun:sqlite's `.changes` is unreliable after RETURNING (see the
// note in SessionStore.ts), so the claim reads the returned row instead.
//
// The `excluded.observation_epoch > ...` guard also keeps the stored epoch
// monotonic: a hook that finishes late carrying an older epoch neither wins the
// claim nor rolls the row back to that stale value.
export const CLAIM_GATE_SQL = `
  INSERT INTO file_context_injections
    (session_id, file_path, observation_epoch, injected_at_epoch)
  VALUES (?, ?, ?, ?)
  ON CONFLICT(session_id, file_path) DO UPDATE SET
    observation_epoch = excluded.observation_epoch,
    injected_at_epoch = excluded.injected_at_epoch
  WHERE excluded.observation_epoch > file_context_injections.observation_epoch
  RETURNING 1 AS claimed
`;

