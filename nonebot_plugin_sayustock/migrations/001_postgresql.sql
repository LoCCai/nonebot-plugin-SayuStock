CREATE SCHEMA qiqi_sayustock;

CREATE TABLE qiqi_sayustock.watchlist (
    owner_key text PRIMARY KEY,
    items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array')
);

CREATE TABLE qiqi_sayustock.artifact (
    id text PRIMARY KEY,
    record jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object')
);
