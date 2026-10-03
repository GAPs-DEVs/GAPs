-- Query indexes. Foreign keys are not automatically indexed by PostgreSQL.

BEGIN;

CREATE INDEX idx_oauth_identities_user_id
    ON oauth_identities (user_id);

CREATE INDEX idx_scores_owner_updated_at
    ON scores (owner_user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_scores_public_updated_at
    ON scores (updated_at DESC)
    WHERE visibility = 'public' AND parser_status = 'ready' AND deleted_at IS NULL;

CREATE INDEX idx_score_assets_score_id
    ON score_assets (score_id);

CREATE INDEX idx_score_versions_score_created_at
    ON score_versions (score_id, created_at DESC);

CREATE INDEX idx_score_members_user_id
    ON score_members (user_id, score_id);

CREATE INDEX idx_user_score_library_user_opened_at
    ON user_score_library (user_id, last_opened_at DESC);

CREATE UNIQUE INDEX uq_user_instruments_default
    ON user_instruments (user_id)
    WHERE is_default;

CREATE INDEX idx_practice_sessions_user_started_at
    ON practice_sessions (user_id, started_at DESC);

CREATE INDEX idx_practice_sessions_version_started_at
    ON practice_sessions (score_version_id, started_at DESC);

CREATE INDEX idx_user_score_progress_user_practiced_at
    ON user_score_progress (user_id, last_practiced_at DESC);

COMMIT;
