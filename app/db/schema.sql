# будущая схема Postgres (на будущее)

-- table #2
CREATE TABLE IF NOT EXISTS user_subscriptions (
  chat_id BIGINT PRIMARY KEY,
  date DATE NOT NULL,
  num_request INTEGER,
  subscribe SMALLINT NOT NULL DEFAULT 0,
  total_requests INTEGER NOT NULL DEFAULT 0,
  payment_date DATE,
  end_payment_date DATE,
  ban_until DATE
);

-- table #3
CREATE TABLE IF NOT EXISTS users (
  chat_id BIGINT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  name TEXT,
  gender TEXT,
  age INTEGER,
  consented SMALLINT NOT NULL DEFAULT 0,
  memory TEXT,
  end_dialog SMALLINT NOT NULL DEFAULT 0
);

-- table #1
CREATE TABLE IF NOT EXISTS requests_log (
  id BIGSERIAL PRIMARY KEY,
  date TIMESTAMPTZ NOT NULL,
  chat_id BIGINT NOT NULL REFERENCES user_subscriptions(chat_id),
  input TEXT NOT NULL,
  output TEXT NOT NULL,
  summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_log_chat_day ON requests_log(chat_id, date);
