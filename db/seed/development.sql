-- Development and demo data only. Never run this file in production.

BEGIN;

INSERT INTO users (
    id,
    display_name,
    role,
    terms_version,
    terms_agreed_at,
    privacy_policy_version,
    privacy_policy_agreed_at,
    last_login_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000001',
        '데모 연습자',
        'user',
        'v1.0',
        CURRENT_TIMESTAMP,
        'v1.0',
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    ),
    (
        '00000000-0000-4000-8000-000000000002',
        '데모 관리자',
        'admin',
        'v1.0',
        CURRENT_TIMESTAMP,
        'v1.0',
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO oauth_identities (
    id,
    user_id,
    provider,
    provider_subject,
    email,
    email_verified,
    last_authenticated_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000011',
        '00000000-0000-4000-8000-000000000001',
        'google',
        'demo-google-subject-001',
        'practice.demo@example.test',
        TRUE,
        CURRENT_TIMESTAMP
    ),
    (
        '00000000-0000-4000-8000-000000000012',
        '00000000-0000-4000-8000-000000000002',
        'kakao',
        'demo-kakao-subject-002',
        'admin.demo@example.test',
        TRUE,
        CURRENT_TIMESTAMP
    )
ON CONFLICT (provider, provider_subject) DO NOTHING;

INSERT INTO scores (
    id,
    owner_user_id,
    title,
    description,
    visibility,
    source_type,
    parser_status
)
VALUES
    (
        '00000000-0000-4000-8000-000000000101',
        '00000000-0000-4000-8000-000000000002',
        '기본 연습 악보',
        '현재 examples/score_practice_sample.json에 대응하는 개발용 악보입니다.',
        'public',
        'sample',
        'ready'
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO score_assets (
    id,
    score_id,
    asset_type,
    storage_provider,
    storage_key,
    original_filename,
    content_type
)
VALUES
    (
        '00000000-0000-4000-8000-000000000111',
        '00000000-0000-4000-8000-000000000101',
        'parsed_json',
        'local',
        'examples/score_practice_sample.json',
        'score_practice_sample.json',
        'application/json'
    )
ON CONFLICT (storage_provider, storage_key) DO NOTHING;

INSERT INTO score_versions (
    id,
    score_id,
    version_number,
    source_asset_id,
    created_by_user_id,
    parser_name,
    parser_version,
    note_count,
    step_count
)
VALUES
    (
        '00000000-0000-4000-8000-000000000201',
        '00000000-0000-4000-8000-000000000101',
        1,
        '00000000-0000-4000-8000-000000000111',
        '00000000-0000-4000-8000-000000000002',
        'sample-json-importer',
        '1.0',
        4,
        3
    )
ON CONFLICT (score_id, version_number) DO NOTHING;

INSERT INTO score_steps (
    id,
    score_version_id,
    sequence_no,
    score_line,
    measure_no,
    beat_no,
    duration_ms
)
VALUES
    (
        '00000000-0000-4000-8000-000000000301',
        '00000000-0000-4000-8000-000000000201',
        1, 1, 1, 1, 500
    ),
    (
        '00000000-0000-4000-8000-000000000302',
        '00000000-0000-4000-8000-000000000201',
        2, 1, 1, 2, 500
    ),
    (
        '00000000-0000-4000-8000-000000000303',
        '00000000-0000-4000-8000-000000000201',
        3, 1, 1, 3, 750
    )
ON CONFLICT (score_version_id, sequence_no) DO NOTHING;

INSERT INTO score_notes (
    id,
    score_step_id,
    string_number,
    fret_number,
    midi_pitch
)
VALUES
    (
        '00000000-0000-4000-8000-000000000401',
        '00000000-0000-4000-8000-000000000301',
        5, 3, 48
    ),
    (
        '00000000-0000-4000-8000-000000000402',
        '00000000-0000-4000-8000-000000000302',
        5, 5, 50
    ),
    (
        '00000000-0000-4000-8000-000000000403',
        '00000000-0000-4000-8000-000000000303',
        4, 2, 52
    ),
    (
        '00000000-0000-4000-8000-000000000404',
        '00000000-0000-4000-8000-000000000303',
        3, 0, 55
    )
ON CONFLICT (score_step_id, string_number) DO NOTHING;

INSERT INTO user_score_library (
    user_id,
    score_id,
    saved_score_version_id,
    is_favorite,
    last_opened_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000101',
        '00000000-0000-4000-8000-000000000201',
        TRUE,
        CURRENT_TIMESTAMP
    )
ON CONFLICT (user_id, score_id) DO NOTHING;

INSERT INTO user_instruments (
    id,
    user_id,
    label,
    tuning_midi,
    is_default
)
VALUES
    (
        '00000000-0000-4000-8000-000000000501',
        '00000000-0000-4000-8000-000000000001',
        '표준 기타',
        '[64, 59, 55, 50, 45, 40]'::jsonb,
        TRUE
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO practice_sessions (
    id,
    user_id,
    score_version_id,
    mode,
    status,
    completed_step_count,
    correct_step_count,
    started_at,
    ended_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000601',
        '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000201',
        'interactive',
        'completed',
        3,
        2,
        CURRENT_TIMESTAMP - INTERVAL '10 minutes',
        CURRENT_TIMESTAMP - INTERVAL '5 minutes'
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO practice_step_results (
    id,
    practice_session_id,
    score_version_id,
    score_step_id,
    attempt_no,
    outcome,
    elapsed_ms,
    detection_summary
)
VALUES
    (
        '00000000-0000-4000-8000-000000000701',
        '00000000-0000-4000-8000-000000000601',
        '00000000-0000-4000-8000-000000000201',
        '00000000-0000-4000-8000-000000000301',
        1,
        'correct',
        820,
        '{"status": "correct", "detected_frequency_hz": 130.81}'::jsonb
    ),
    (
        '00000000-0000-4000-8000-000000000702',
        '00000000-0000-4000-8000-000000000601',
        '00000000-0000-4000-8000-000000000201',
        '00000000-0000-4000-8000-000000000302',
        1,
        'incorrect',
        1200,
        '{"status": "incorrect", "detected_frequency_hz": 146.83}'::jsonb
    ),
    (
        '00000000-0000-4000-8000-000000000703',
        '00000000-0000-4000-8000-000000000601',
        '00000000-0000-4000-8000-000000000201',
        '00000000-0000-4000-8000-000000000303',
        1,
        'correct',
        980,
        '{"status": "correct", "detected_notes": ["4번줄 2프렛", "3번줄 0프렛"]}'::jsonb
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO user_score_progress (
    user_id,
    score_version_id,
    last_completed_step_no,
    best_accuracy_percent,
    last_practiced_at
)
VALUES
    (
        '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000201',
        3,
        66.67,
        CURRENT_TIMESTAMP - INTERVAL '5 minutes'
    )
ON CONFLICT (user_id, score_version_id) DO NOTHING;

COMMIT;
