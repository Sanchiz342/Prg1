CREATE TABLE api_keys (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    key_hash    BYTEA       NOT NULL UNIQUE,  -- sha256 of the key; the key itself is shown once and never stored
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE links (
    id           BIGSERIAL PRIMARY KEY,
    code         TEXT        NOT NULL UNIQUE,
    target_url   TEXT        NOT NULL,
    owner_id     BIGINT      NOT NULL REFERENCES api_keys(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    disabled     BOOLEAN     NOT NULL DEFAULT false,
    click_count  BIGINT      NOT NULL DEFAULT 0  -- denormalised, maintained by the click writer
);
CREATE INDEX links_owner_id ON links (owner_id, id DESC);

-- One row per redirect. No raw IP addresses are stored: visitor_hash is a salted, daily-rotating hash.
CREATE TABLE clicks (
    event_id      UUID        PRIMARY KEY,    -- makes at-least-once stream delivery idempotent
    link_id       BIGINT      NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    ts            TIMESTAMPTZ NOT NULL,
    visitor_hash  BYTEA       NOT NULL,
    country       CHAR(2)     NOT NULL,       -- ISO 3166-1 alpha-2, 'ZZ' = unknown
    referrer_host TEXT        NOT NULL DEFAULT '',
    device        TEXT        NOT NULL,       -- desktop | mobile | tablet | bot | other
    browser       TEXT        NOT NULL,
    is_bot        BOOLEAN     NOT NULL DEFAULT false
);
CREATE INDEX clicks_link_ts ON clicks (link_id, ts);
