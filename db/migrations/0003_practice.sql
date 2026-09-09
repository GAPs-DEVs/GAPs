-- Per-user instrument settings and practice outcomes.
-- Only derived detection results are stored; raw microphone and camera media are intentionally excluded.

BEGIN;

CREATE TABLE user_instruments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label VARCHAR(80) NOT NULL,
    string_count SMALLINT NOT NULL DEFAULT 6 CHECK (string_count BETWEEN 1 AND 12),
    tuning_midi JSONB NOT NULL DEFAULT '[64, 59, 55, 50, 45, 40]'::jsonb,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_instruments_user_label UNIQUE (user_id, label),
    CHECK (jsonb_typeof(tuning_midi) = 'array')
);

CREATE TABLE practice_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    score_version_id UUID NOT NULL REFERENCES score_versions(id) ON DELETE RESTRICT,
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('interactive', 'auto')),
    status VARCHAR(16) NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'abandoned')),
    speed_multiplier NUMERIC(4, 2) NOT NULL DEFAULT 1.00
        CHECK (speed_multiplier BETWEEN 0.05 AND 2.00),
    started_step_no INTEGER NOT NULL DEFAULT 1 CHECK (started_step_no >= 1),
    completed_step_count INTEGER NOT NULL DEFAULT 0 CHECK (completed_step_count >= 0),
    correct_step_count INTEGER NOT NULL DEFAULT 0 CHECK (correct_step_count >= 0),
    client_version VARCHAR(64),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_practice_sessions_ended_after_started CHECK (
        ended_at IS NULL OR ended_at >= started_at
    ),
    CONSTRAINT ck_practice_sessions_correct_not_greater_than_completed CHECK (
        correct_step_count <= completed_step_count
    ),
    CONSTRAINT uq_practice_sessions_id_version UNIQUE (id, score_version_id)
);

CREATE TABLE practice_step_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_session_id UUID NOT NULL,
    score_version_id UUID NOT NULL,
    score_step_id UUID NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
    outcome VARCHAR(16) NOT NULL
        CHECK (outcome IN ('correct', 'incorrect', 'skipped', 'timeout')),
    elapsed_ms INTEGER CHECK (elapsed_ms IS NULL OR elapsed_ms >= 0),
    retrigger_required BOOLEAN NOT NULL DEFAULT FALSE,
    detection_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_practice_step_results_session_version
        FOREIGN KEY (practice_session_id, score_version_id)
        REFERENCES practice_sessions (id, score_version_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_practice_step_results_step_version
        FOREIGN KEY (score_step_id, score_version_id)
        REFERENCES score_steps (id, score_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_practice_step_results_attempt UNIQUE (
        practice_session_id, score_step_id, attempt_no
    ),
    CHECK (jsonb_typeof(detection_summary) = 'object')
);

CREATE TABLE user_score_progress (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score_version_id UUID NOT NULL REFERENCES score_versions(id) ON DELETE CASCADE,
    last_completed_step_no INTEGER NOT NULL DEFAULT 0 CHECK (last_completed_step_no >= 0),
    best_accuracy_percent NUMERIC(5, 2) CHECK (
        best_accuracy_percent IS NULL
        OR best_accuracy_percent BETWEEN 0 AND 100
    ),
    last_practiced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, score_version_id)
);

CREATE TRIGGER trg_user_instruments_set_updated_at
BEFORE UPDATE ON user_instruments
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

CREATE TRIGGER trg_practice_sessions_set_updated_at
BEFORE UPDATE ON practice_sessions
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

CREATE TRIGGER trg_user_score_progress_set_updated_at
BEFORE UPDATE ON user_score_progress
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

COMMENT ON TABLE practice_sessions IS '한 번의 연습 실행. score_version_id를 참조하므로 악보 수정 후에도 기존 결과가 보존된다.';
COMMENT ON TABLE practice_step_results IS '연습 중 step별 판정 이력. detection_summary에는 판정값만 저장하고 원본 오디오·영상은 저장하지 않는다.';
COMMENT ON TABLE user_score_progress IS '사용자별 악보 버전 진행 상태를 빠르게 조회하기 위한 요약 데이터.';

COMMIT;
