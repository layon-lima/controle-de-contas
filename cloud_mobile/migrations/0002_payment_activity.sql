CREATE TABLE IF NOT EXISTS payment_activity (
  account_id INTEGER PRIMARY KEY,
  suggested_at TEXT,
  suggested_until TEXT,
  copied_at TEXT,
  copied_until TEXT,
  copy_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payment_activity_suggested_until
  ON payment_activity(suggested_until);

CREATE INDEX IF NOT EXISTS idx_payment_activity_copied_until
  ON payment_activity(copied_until);
