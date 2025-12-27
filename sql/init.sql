-- Huginn database schema
-- Run: psql -U huginn -d huginn -f init.sql
-- Or: mounted as /docker-entrypoint-initdb.d/init.sql for auto-init

CREATE TABLE systems (
    id64 BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    z DOUBLE PRECISION NOT NULL,

    power TEXT,
    power_state TEXT,

    is_interested BOOLEAN DEFAULT FALSE,
    is_candidate BOOLEAN DEFAULT FALSE,

    has_ring BOOLEAN DEFAULT FALSE,
    has_cnb BOOLEAN DEFAULT FALSE,
    has_haz_res BOOLEAN DEFAULT FALSE,
    has_high_res BOOLEAN DEFAULT FALSE,
    has_med_res BOOLEAN DEFAULT FALSE,
    has_low_res BOOLEAN DEFAULT FALSE,

    spansh_updated_at TIMESTAMPTZ,
    inara_info_updated_at TIMESTAMPTZ,
    inara_factions_updated_at TIMESTAMPTZ,
    res_info_updated_at TIMESTAMPTZ,
    candidacy_checked_at TIMESTAMPTZ,

    metadata JSONB DEFAULT '{}'::JSONB,
    data JSONB DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Lookup by name
CREATE INDEX idx_systems_name ON systems(name);

-- Filtered indexes for common queries
CREATE INDEX idx_systems_interested ON systems(id64) WHERE is_interested;
CREATE INDEX idx_systems_candidate ON systems(id64) WHERE is_candidate;
