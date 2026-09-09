-- OAuth-only identity and member profile tables.
-- Apply this file before all later migrations.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION app_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name VARCHAR(80) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'user'
        CHECK (role IN ('user', 'admin')),
    status VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'deleted')),
    terms_version VARCHAR(32),
    terms_agreed_at TIMESTAMPTZ,
    privacy_policy_version VARCHAR(32),
    privacy_policy_agreed_at TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ,
    CHECK (
        (terms_version IS NULL AND terms_agreed_at IS NULL)
        OR (terms_version IS NOT NULL AND terms_agreed_at IS NOT NULL)
    ),
    CHECK (
        (privacy_policy_version IS NULL AND privacy_policy_agreed_at IS NULL)
        OR (privacy_policy_version IS NOT NULL AND privacy_policy_agreed_at IS NOT NULL)
    )
);

CREATE TABLE oauth_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    provider VARCHAR(32) NOT NULL,
    provider_subject VARCHAR(255) NOT NULL,
    email VARCHAR(320),
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_authenticated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_oauth_identities_provider_subject UNIQUE (provider, provider_subject)
);

CREATE TRIGGER trg_users_set_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

CREATE TRIGGER trg_oauth_identities_set_updated_at
BEFORE UPDATE ON oauth_identities
FOR EACH ROW
EXECUTE FUNCTION app_set_updated_at();

COMMENT ON TABLE users IS '서비스 내부 회원 프로필. OAuth 비밀번호 또는 토큰을 저장하지 않는다.';
COMMENT ON TABLE oauth_identities IS 'OAuth/OIDC 제공자 계정과 내부 회원을 연결한다. provider_subject가 실제 외부 계정 식별자다.';
COMMENT ON COLUMN oauth_identities.provider_subject IS 'OIDC의 sub 등 제공자가 보장하는 불변 식별자. 이메일을 로그인 식별자로 사용하지 않는다.';

COMMIT;
