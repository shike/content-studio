CREATE TABLE account_metrics_daily (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	date VARCHAR NOT NULL, 
	fans INTEGER NOT NULL, 
	new_fans INTEGER NOT NULL, 
	cancel_fans INTEGER NOT NULL, 
	profile_views INTEGER NOT NULL, 
	play INTEGER NOT NULL, 
	digg INTEGER NOT NULL, 
	comment INTEGER NOT NULL, 
	share INTEGER NOT NULL, 
	account_search INTEGER NOT NULL, 
	post_search INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE app_settings (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	"key" VARCHAR NOT NULL, 
	value VARCHAR NOT NULL, 
	PRIMARY KEY ("key")
);
CREATE TABLE articles (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	topic_id INTEGER, 
	script_id INTEGER, 
	md VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	note VARCHAR NOT NULL, 
	kb_sources JSON, 
	publish_url VARCHAR NOT NULL, 
	published_at VARCHAR NOT NULL, 
	stats JSON, 
	html VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(topic_id) REFERENCES topics (id), 
	FOREIGN KEY(script_id) REFERENCES scripts (id)
);
CREATE TABLE benchmark_videos (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	url VARCHAR NOT NULL, 
	author VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	stats JSON, 
	transcript VARCHAR NOT NULL, 
	analysis JSON, 
	top_comments JSON, 
	comment_kind VARCHAR NOT NULL, 
	comment_leadgen_share FLOAT NOT NULL, 
	media_path VARCHAR NOT NULL, 
	quality_score FLOAT NOT NULL, 
	account_type VARCHAR NOT NULL, 
	notes VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	scan_status VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE content_items (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	media_id VARCHAR NOT NULL, 
	folder_name VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	source_author VARCHAR NOT NULL, 
	quality_score FLOAT NOT NULL, 
	why_good VARCHAR NOT NULL, 
	adapt_angle VARCHAR NOT NULL, 
	best_quotes JSON, 
	category VARCHAR NOT NULL, 
	priority INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	article_id INTEGER, 
	note VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE daily_ledger (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	day VARCHAR NOT NULL, 
	phase VARCHAR NOT NULL, 
	produced VARCHAR NOT NULL, 
	reason VARCHAR NOT NULL, 
	ok BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE failure_patterns (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	fp VARCHAR NOT NULL, 
	fail_class VARCHAR NOT NULL, 
	label VARCHAR NOT NULL, 
	root_cause VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	count INTEGER NOT NULL, 
	sample_error VARCHAR NOT NULL, 
	sample_job_id INTEGER NOT NULL, 
	last_type VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (fp)
);
CREATE TABLE falsify_items (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	claim VARCHAR NOT NULL, 
	target VARCHAR NOT NULL, 
	evidence_note VARCHAR NOT NULL, 
	result_note VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	auto_source BOOLEAN NOT NULL, 
	script_id INTEGER, 
	PRIMARY KEY (id)
);
CREATE TABLE jobs (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	type VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	progress INTEGER NOT NULL, 
	message VARCHAR NOT NULL, 
	payload JSON, 
	result JSON, 
	history JSON, 
	error VARCHAR, 
	dedup_key VARCHAR NOT NULL, 
	lane VARCHAR NOT NULL, 
	retry_count INTEGER NOT NULL, 
	max_retries INTEGER NOT NULL, 
	next_retry_at DATETIME, 
	fail_class VARCHAR NOT NULL, 
	error_fp VARCHAR NOT NULL, 
	archived INTEGER NOT NULL, 
	retry_of INTEGER NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE radar_topics (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	ch_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	notify BOOLEAN NOT NULL, 
	seen_ids JSON, 
	hits JSON, 
	last_checked_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (ch_id)
);
CREATE TABLE research_reports (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	topic_id INTEGER, 
	content VARCHAR NOT NULL, 
	model VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(topic_id) REFERENCES topics (id)
);
CREATE TABLE schedules (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	"key" VARCHAR NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	hour INTEGER, 
	last_run_at DATETIME, 
	next_run_at DATETIME, 
	last_status VARCHAR NOT NULL, 
	run_count INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE ("key")
);
CREATE TABLE script_templates (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	structure JSON, 
	source_benchmark_id INTEGER, 
	usage_count INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(source_benchmark_id) REFERENCES benchmark_videos (id)
);
CREATE TABLE scripts (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	topic_id INTEGER, 
	card_id INTEGER, 
	falsify_item_id INTEGER, 
	ore_id INTEGER, 
	title_hint VARCHAR NOT NULL, 
	versions JSON, 
	final_text VARCHAR NOT NULL, 
	teleprompter_text VARCHAR NOT NULL, 
	storyboard JSON, 
	title_candidates JSON, 
	status VARCHAR NOT NULL, 
	account_type VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(topic_id) REFERENCES topics (id), 
	FOREIGN KEY(card_id) REFERENCES topic_cards (id), 
	FOREIGN KEY(falsify_item_id) REFERENCES falsify_items (id), 
	FOREIGN KEY(ore_id) REFERENCES benchmark_videos (id)
);
CREATE TABLE self_videos (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	url VARCHAR NOT NULL, 
	aweme_id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	media_path VARCHAR NOT NULL, 
	transcript VARCHAR NOT NULL, 
	scan_status VARCHAR NOT NULL, 
	style_analysis JSON, 
	analyzed_at DATETIME, 
	stats JSON, 
	published_at VARCHAR NOT NULL, 
	duration INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE style_profiles (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	account_name VARCHAR NOT NULL, 
	version INTEGER NOT NULL, 
	digest VARCHAR NOT NULL, 
	traits JSON, 
	exemplars JSON, 
	based_on INTEGER NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE tenants (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	brand JSON, 
	PRIMARY KEY (id)
);
CREATE TABLE topic_cards (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	batch_date VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	pool JSON, 
	spine VARCHAR NOT NULL, 
	hook VARCHAR NOT NULL, 
	spice VARCHAR NOT NULL, 
	takeaways JSON, 
	judge JSON, 
	gates JSON, 
	status VARCHAR NOT NULL, 
	superseded_by INTEGER, 
	script_id INTEGER, 
	PRIMARY KEY (id)
);
CREATE TABLE topics (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	line VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	angle VARCHAR NOT NULL, 
	audience VARCHAR NOT NULL, 
	source_type VARCHAR NOT NULL, 
	source_ref VARCHAR NOT NULL, 
	evidence JSON, 
	score FLOAT, 
	score_breakdown JSON, 
	status VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE user_sessions (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	token_hash VARCHAR NOT NULL, 
	user_id INTEGER NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	expires_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
);
CREATE TABLE users (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	id INTEGER NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	username VARCHAR NOT NULL, 
	password_hash VARCHAR NOT NULL, 
	role VARCHAR NOT NULL, 
	display_name VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
CREATE TABLE video_weekly_reports (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	week_start VARCHAR NOT NULL, 
	report JSON, 
	PRIMARY KEY (id)
);
CREATE TABLE watch_accounts (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	platform VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	url VARCHAR NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	kind VARCHAR NOT NULL, 
	creator_cookie VARCHAR NOT NULL, 
	note VARCHAR NOT NULL, 
	account_type VARCHAR NOT NULL, 
	last_scan_at DATETIME, 
	PRIMARY KEY (id)
);
CREATE TABLE watch_candidates (
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	tenant_id INTEGER NOT NULL, 
	id INTEGER NOT NULL, 
	keyword VARCHAR NOT NULL, 
	direction VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	sec_uid VARCHAR NOT NULL, 
	url VARCHAR NOT NULL, 
	signature VARCHAR NOT NULL, 
	follower_count INTEGER NOT NULL, 
	sample_video_id VARCHAR NOT NULL, 
	sample_title VARCHAR NOT NULL, 
	sample_stats VARCHAR NOT NULL, 
	account_type VARCHAR NOT NULL, 
	leadgen_density FLOAT NOT NULL, 
	pure_reason VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_account_metrics_daily_date ON account_metrics_daily (date);
CREATE INDEX ix_account_metrics_daily_tenant_id ON account_metrics_daily (tenant_id);
CREATE INDEX ix_articles_tenant ON articles(tenant_id);
CREATE INDEX ix_articles_tenant_id ON articles (tenant_id);
CREATE INDEX ix_benchmark_videos_tenant ON benchmark_videos(tenant_id);
CREATE INDEX ix_benchmark_videos_tenant_id ON benchmark_videos (tenant_id);
CREATE INDEX ix_content_items_article_id ON content_items (article_id);
CREATE INDEX ix_content_items_category ON content_items (category);
CREATE INDEX ix_content_items_media_id ON content_items (media_id);
CREATE INDEX ix_content_items_priority ON content_items (priority);
CREATE INDEX ix_content_items_status ON content_items (status);
CREATE INDEX ix_content_items_tenant_id ON content_items (tenant_id);
CREATE INDEX ix_daily_ledger_day ON daily_ledger (day);
CREATE INDEX ix_daily_ledger_tenant_id ON daily_ledger (tenant_id);
CREATE INDEX ix_failure_patterns_status ON failure_patterns (status);
CREATE INDEX ix_falsify_items_status ON falsify_items (status);
CREATE INDEX ix_falsify_items_tenant_id ON falsify_items (tenant_id);
CREATE UNIQUE INDEX ix_jobs_active_dedup ON jobs(tenant_id, type, dedup_key) WHERE dedup_key <> '' AND status IN ('queued','running','parked');
CREATE INDEX ix_jobs_dedup_key ON jobs (dedup_key);
CREATE INDEX ix_jobs_error_fp ON jobs (error_fp);
CREATE INDEX ix_jobs_status ON jobs (status);
CREATE INDEX ix_jobs_tenant_id ON jobs (tenant_id);
CREATE INDEX ix_jobs_type ON jobs (type);
CREATE INDEX ix_jobs_user_id ON jobs (user_id);
CREATE INDEX ix_radar_topics_tenant ON radar_topics(tenant_id);
CREATE INDEX ix_radar_topics_tenant_id ON radar_topics (tenant_id);
CREATE INDEX ix_research_reports_tenant ON research_reports(tenant_id);
CREATE INDEX ix_research_reports_tenant_id ON research_reports (tenant_id);
CREATE INDEX ix_research_reports_topic_id ON research_reports (topic_id);
CREATE INDEX ix_script_templates_name ON script_templates (name);
CREATE INDEX ix_scripts_card_id ON scripts (card_id);
CREATE INDEX ix_scripts_falsify_item_id ON scripts (falsify_item_id);
CREATE INDEX ix_scripts_ore_id ON scripts (ore_id);
CREATE INDEX ix_scripts_tenant ON scripts(tenant_id);
CREATE INDEX ix_scripts_tenant_id ON scripts (tenant_id);
CREATE INDEX ix_scripts_topic_id ON scripts (topic_id);
CREATE INDEX ix_self_videos_aweme_id ON self_videos (aweme_id);
CREATE INDEX ix_self_videos_tenant ON self_videos(tenant_id);
CREATE INDEX ix_self_videos_tenant_id ON self_videos (tenant_id);
CREATE INDEX ix_style_profiles_tenant ON style_profiles(tenant_id);
CREATE INDEX ix_style_profiles_tenant_id ON style_profiles (tenant_id);
CREATE INDEX ix_topic_cards_batch_date ON topic_cards (batch_date);
CREATE INDEX ix_topic_cards_status ON topic_cards (status);
CREATE INDEX ix_topic_cards_tenant_id ON topic_cards (tenant_id);
CREATE INDEX ix_topics_line ON topics (line);
CREATE INDEX ix_topics_tenant ON topics(tenant_id);
CREATE INDEX ix_topics_tenant_id ON topics (tenant_id);
CREATE INDEX ix_user_sessions_expires_at ON user_sessions (expires_at);
CREATE INDEX ix_user_sessions_tenant_id ON user_sessions (tenant_id);
CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);
CREATE INDEX ix_users_role ON users (role);
CREATE INDEX ix_users_status ON users (status);
CREATE INDEX ix_users_tenant_id ON users (tenant_id);
CREATE INDEX ix_video_weekly_reports_tenant_id ON video_weekly_reports (tenant_id);
CREATE INDEX ix_video_weekly_reports_week_start ON video_weekly_reports (week_start);
CREATE INDEX ix_watch_accounts_tenant ON watch_accounts(tenant_id);
CREATE INDEX ix_watch_accounts_tenant_id ON watch_accounts (tenant_id);
CREATE INDEX ix_watch_candidates_sec_uid ON watch_candidates (sec_uid);
CREATE INDEX ix_watch_candidates_status ON watch_candidates (status);
CREATE INDEX ix_watch_candidates_tenant ON watch_candidates(tenant_id);
CREATE INDEX ix_watch_candidates_tenant_id ON watch_candidates (tenant_id);
