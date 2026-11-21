CREATE TABLE IF NOT EXISTS qiqi_sayustock.news_subscription (
    owner_key text PRIMARY KEY,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object')
);
