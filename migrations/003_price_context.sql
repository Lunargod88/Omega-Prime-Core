ALTER TABLE decision_ledger
ADD COLUMN entry_price NUMERIC,
ADD COLUMN stop_price NUMERIC,
ADD COLUMN min_target NUMERIC,
ADD COLUMN max_target NUMERIC,
ADD COLUMN current_price NUMERIC;
