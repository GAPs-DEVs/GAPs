-- Score ownership, files, immutable versions, and parsed practice notes.
-- Existing JSON fields map as follows:
-- step -> score_steps.sequence_no, string/fret/midi_pitch -> score_notes,
-- duration_ms -> score_steps.duration_ms, score_line/measure/beat -> score_steps.

BEGIN;

CREATE TABLE scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    title VARCHAR(200) NOT NULL CHECK (char_length(btrim(title)) > 0),
    description TEXT,
    visibility VARCHAR(16) NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('private', 'unlisted', 'public')),
    source_type VARCHAR(24) NOT NULL
        CHECK (source_type IN ('guitar_pro', 'image', 'manual', 'sample', 'import')),
    parser_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (parser_status IN ('pending', 'processing', 'ready', 'failed')),
    parser_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE score_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    score_id UUID NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    asset_type VARCHAR(24) NOT NULL
        CHECK (asset_type IN ('original_file', 'parsed_json', 'score_image', 'thumbnail')),
    storage_provider VARCHAR(16) NOT NULL DEFAULT 's3'
        CHECK (storage_provider IN ('s3', 'local')),
    storage_key TEXT NOT NULL,
    original_filename VARCHAR(512),
    content_type VARCHAR(127),
    byte_size BIGINT CHECK (byte_size IS NULL OR byte_size >= 0),
    checksum_sha256 CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_score_assets_id_score UNIQUE (id, score_id),
    CONSTRAINT uq_score_assets_storage_key UNIQUE (storage_provider, storage_key)
);

CREATE TABLE score_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    score_id UUID NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    source_asset_id UUID,
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    parser_name VARCHAR(64),
    parser_version VARCHAR(64),
    note_count INTEGER NOT NULL DEFAULT 0 CHECK (note_count >= 0),
    step_count INTEGER NOT NULL DEFAULT 0 CHECK (step_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_score_versions_score_number UNIQUE (score_id, version_number),
    CONSTRAINT uq_score_versions_id_score UNIQUE (id, score_id),
    CONSTRAINT fk_score_versions_source_asset
        FOREIGN KEY (source_asset_id, score_id)
        REFERENCES score_assets (id, score_id)
        ON DELETE RESTRICT
);

CREATE TABLE score_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    score_version_id UUID NOT NULL REFERENCES score_versions(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    score_line INTEGER CHECK (score_line >= 1),
    measure_no INTEGER CHECK (measure_no >= 1),
    beat_no INTEGER CHECK (beat_no >= 1),
    duration_ms INTEGER NOT NULL DEFAULT 500 CHECK (duration_ms >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_score_steps_version_sequence UNIQUE (score_version_id, sequence_no),
    CONSTRAINT uq_score_steps_id_version UNIQUE (id, score_version_id)
);

CREATE TABLE score_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    score_step_id UUID NOT NULL REFERENCES score_steps(id) ON DELETE CASCADE,
    string_number SMALLINT NOT NULL CHECK (string_number BETWEEN 1 AND 6),
    fret_number SMALLINT NOT NULL CHECK (fret_number BETWEEN 0 AND 30),
    midi_pitch SMALLINT CHECK (midi_pitch BETWEEN 0 AND 127),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_score_notes_step_string UNIQUE (score_step_id, string_number)
);

CREATE TABLE score_members (
    score_id UUID NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission VARCHAR(16) NOT NULL
        CHECK (permission IN ('viewer', 'editor')),
    added_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (score_id, user_id)
);

CREATE TABLE user_score_library (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score_id UUID NOT NULL REFERENCES scores(id) ON DELETE CASCADE,
    saved_score_version_id UUID,
    is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
    saved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_opened_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, score_id),
    CONSTRAINT fk_user_score_library_saved_version
        FOREIGN KEY (saved_score_version_id, score_id)
        REFERENCES score_versions (id, score_id)
        ON DELETE RESTRICT
);

CREATE TRIGGER trg_scores_set_updated_at
BEFORE UPDATE ON scores
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

CREATE TRIGGER trg_user_score_library_set_updated_at
BEFORE UPDATE ON user_score_library
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

COMMENT ON TABLE scores IS '악보의 소유권과 공개 정책을 보관하는 상위 엔터티. 삭제는 deleted_at을 이용한 소프트 삭제가 기본이다.';
COMMENT ON TABLE score_assets IS '원본 GuitarPro/이미지와 변환 JSON의 파일 메타데이터. 실제 파일 본문은 S3 또는 서버 파일 저장소에 둔다.';
COMMENT ON TABLE score_versions IS '악보 수정 이력. 과거 연습 기록은 항상 해당 버전을 참조해 변경되지 않는다.';
COMMENT ON TABLE score_steps IS '연습 진행 단위. 같은 sequence_no를 가진 음표들은 하나의 화음 step을 이룬다.';
COMMENT ON TABLE score_notes IS '각 step의 줄·프렛·MIDI 정보.';

COMMIT;
