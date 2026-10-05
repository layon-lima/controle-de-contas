CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  payload TEXT NOT NULL,
  pdf_hash TEXT NOT NULL DEFAULT '',
  pdf_name TEXT NOT NULL DEFAULT '',
  pdf_pages INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_accounts_updated_at ON accounts(updated_at);
