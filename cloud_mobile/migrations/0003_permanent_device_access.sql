CREATE TABLE IF NOT EXISTS authorized_devices (
  token_hash TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent TEXT NOT NULL DEFAULT '',
  revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_authorized_devices_last_seen
  ON authorized_devices(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_authorized_devices_revoked
  ON authorized_devices(revoked_at);
