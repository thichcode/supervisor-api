ALTER TABLE IF EXISTS interaction_logs
  ADD COLUMN IF NOT EXISTS traffic_class VARCHAR(32);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_traffic_class
  ON interaction_logs(traffic_class);
