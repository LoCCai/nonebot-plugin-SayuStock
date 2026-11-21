-- PostgreSQL simulation ledger, scoped to the stock schema.

BEGIN;

SET LOCAL search_path TO qiqi_sayustock;

CREATE TABLE IF NOT EXISTS sayumacroevent (
	id SERIAL NOT NULL,
	slug VARCHAR NOT NULL,
	title VARCHAR NOT NULL,
	category VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	severity INTEGER NOT NULL,
	direction VARCHAR NOT NULL,
	affected_sectors VARCHAR NOT NULL,
	summary VARCHAR NOT NULL,
	result VARCHAR NOT NULL,
	stance VARCHAR NOT NULL,
	source_urls VARCHAR NOT NULL,
	check_interval_hours INTEGER NOT NULL,
	created_by VARCHAR NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	last_checked_at TIMESTAMP WITHOUT TIME ZONE,
	settled_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT ux_sayumacroevent_slug UNIQUE (slug)
);

CREATE INDEX IF NOT EXISTS ix_sayumacroevent_category ON sayumacroevent (category);

CREATE INDEX IF NOT EXISTS ix_sayumacroevent_slug ON sayumacroevent (slug);

CREATE INDEX IF NOT EXISTS ix_sayumacroevent_status ON sayumacroevent (status);

CREATE TABLE IF NOT EXISTS sayupaperaccount (
	id SERIAL NOT NULL,
	name VARCHAR NOT NULL,
	strategy_id VARCHAR NOT NULL,
	strategy_params VARCHAR NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	cash FLOAT NOT NULL,
	initial_cash FLOAT NOT NULL,
	principal FLOAT NOT NULL,
	mode VARCHAR NOT NULL,
	frequency_minutes INTEGER NOT NULL,
	enabled INTEGER NOT NULL,
	kanban_init_root_id VARCHAR,
	kanban_period_root_id VARCHAR,
	initialized_by VARCHAR,
	schema_migrated_v2 INTEGER NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE,
	started_at TIMESTAMP WITHOUT TIME ZONE,
	last_decided_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT ux_sayupaperaccount_name UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS ix_sayupaperaccount_bot_id ON sayupaperaccount (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperaccount_enabled ON sayupaperaccount (enabled);

CREATE INDEX IF NOT EXISTS ix_sayupaperaccount_group_id ON sayupaperaccount (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperaccount_name ON sayupaperaccount (name);

CREATE INDEX IF NOT EXISTS ix_sayupaperaccount_strategy_id ON sayupaperaccount (strategy_id);

CREATE TABLE IF NOT EXISTS sayupaperagentpool (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	stock_code VARCHAR NOT NULL,
	stock_name VARCHAR NOT NULL,
	secid VARCHAR NOT NULL,
	reason VARCHAR NOT NULL,
	added_by VARCHAR NOT NULL,
	priority INTEGER NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_sayupaperagentpool_account_id ON sayupaperagentpool (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperagentpool_bot_id ON sayupaperagentpool (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperagentpool_group_id ON sayupaperagentpool (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperagentpool_stock_code ON sayupaperagentpool (stock_code);

CREATE TABLE IF NOT EXISTS sayupaperbroadcasttarget (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	bot_id VARCHAR NOT NULL,
	bot_self_id VARCHAR NOT NULL,
	ws_bot_id VARCHAR NOT NULL,
	group_id VARCHAR NOT NULL,
	enabled INTEGER NOT NULL,
	created_by VARCHAR,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ux_sayupaperbroadcast_acc_bot_group UNIQUE (account_id, bot_id, group_id)
);

CREATE INDEX IF NOT EXISTS ix_sayupaperbroadcasttarget_account_id ON sayupaperbroadcasttarget (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperbroadcasttarget_bot_id ON sayupaperbroadcasttarget (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperbroadcasttarget_enabled ON sayupaperbroadcasttarget (enabled);

CREATE INDEX IF NOT EXISTS ix_sayupaperbroadcasttarget_group_id ON sayupaperbroadcasttarget (group_id);

CREATE TABLE IF NOT EXISTS sayupaperdecision (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	action VARCHAR NOT NULL,
	stock_code VARCHAR,
	stock_name VARCHAR,
	score FLOAT NOT NULL,
	reason VARCHAR NOT NULL,
	indicators VARCHAR NOT NULL,
	trade_id INTEGER,
	blocked_by VARCHAR NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_sayupaperdecision_account_id ON sayupaperdecision (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperdecision_action ON sayupaperdecision (action);

CREATE INDEX IF NOT EXISTS ix_sayupaperdecision_bot_id ON sayupaperdecision (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperdecision_created_at ON sayupaperdecision (created_at);

CREATE INDEX IF NOT EXISTS ix_sayupaperdecision_group_id ON sayupaperdecision (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperdecision_stock_code ON sayupaperdecision (stock_code);

CREATE TABLE IF NOT EXISTS sayupaperposition (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	stock_code VARCHAR NOT NULL,
	stock_name VARCHAR NOT NULL,
	secid VARCHAR NOT NULL,
	qty INTEGER NOT NULL,
	avg_cost FLOAT NOT NULL,
	last_quote_price FLOAT,
	last_quote_at TIMESTAMP WITHOUT TIME ZONE,
	opened_at TIMESTAMP WITHOUT TIME ZONE,
	updated_at TIMESTAMP WITHOUT TIME ZONE,
	plan_stop_price FLOAT,
	plan_stop_pct FLOAT,
	plan_take_price FLOAT,
	plan_thesis VARCHAR,
	plan_updated_at TIMESTAMP WITHOUT TIME ZONE,
	plan_rev_count INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_sayupaperposition_account_id ON sayupaperposition (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperposition_bot_id ON sayupaperposition (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperposition_group_id ON sayupaperposition (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperposition_stock_code ON sayupaperposition (stock_code);

CREATE TABLE IF NOT EXISTS sayupapersnapshot (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	trade_date DATE NOT NULL,
	cash FLOAT NOT NULL,
	position_value FLOAT NOT NULL,
	total_equity FLOAT NOT NULL,
	day_pnl FLOAT NOT NULL,
	day_pnl_pct FLOAT NOT NULL,
	total_pnl FLOAT NOT NULL,
	total_pnl_pct FLOAT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_sayupapersnapshot_account_id ON sayupapersnapshot (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupapersnapshot_bot_id ON sayupapersnapshot (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupapersnapshot_group_id ON sayupapersnapshot (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupapersnapshot_trade_date ON sayupapersnapshot (trade_date);

CREATE TABLE IF NOT EXISTS sayupapertrade (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	stock_code VARCHAR NOT NULL,
	stock_name VARCHAR NOT NULL,
	secid VARCHAR NOT NULL,
	side VARCHAR NOT NULL,
	price FLOAT NOT NULL,
	qty INTEGER NOT NULL,
	amount FLOAT NOT NULL,
	fee FLOAT NOT NULL,
	realized_pnl FLOAT NOT NULL,
	reason VARCHAR NOT NULL,
	snapshot VARCHAR NOT NULL,
	decided_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	executed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	decision_id INTEGER,
	mode VARCHAR NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_sayupapertrade_account_id ON sayupapertrade (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupapertrade_bot_id ON sayupapertrade (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupapertrade_decided_at ON sayupapertrade (decided_at);

CREATE INDEX IF NOT EXISTS ix_sayupapertrade_group_id ON sayupapertrade (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupapertrade_stock_code ON sayupapertrade (stock_code);

CREATE TABLE IF NOT EXISTS sayupaperwatchlist (
	id SERIAL NOT NULL,
	account_id INTEGER NOT NULL,
	group_id VARCHAR NOT NULL,
	bot_id VARCHAR NOT NULL,
	user_id VARCHAR NOT NULL,
	stock_code VARCHAR NOT NULL,
	stock_name VARCHAR NOT NULL,
	secid VARCHAR NOT NULL,
	note VARCHAR NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_sayupaperwatchlist_account_id ON sayupaperwatchlist (account_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperwatchlist_bot_id ON sayupaperwatchlist (bot_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperwatchlist_group_id ON sayupaperwatchlist (group_id);

CREATE INDEX IF NOT EXISTS ix_sayupaperwatchlist_stock_code ON sayupaperwatchlist (stock_code);

CREATE INDEX IF NOT EXISTS ix_sayupaperwatchlist_user_id ON sayupaperwatchlist (user_id);

COMMIT;
