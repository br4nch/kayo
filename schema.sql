CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;


CREATE TABLE IF NOT EXISTS blacklist (
    user_id BIGINT UNIQUE NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS whitelist (
    guild_id BIGINT UNIQUE NOT NULL,
    user_id BIGINT NOT NULL,
    receipt_id TEXT NOT NULL,
    PRIMARY KEY (user_id, receipt_id)
);

CREATE TABLE IF NOT EXISTS settings (
    guild_id BIGINT UNIQUE NOT NULL,
    prefix TEXT,
    mod_log_channel_id BIGINT,
    backup_task BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS cases (
    id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    message_id BIGINT,
    reason TEXT NOT NULL DEFAULT 'No reason provided',
    "action" BIGINT NOT NULL DEFAULT 0,
    action_expiration TIMESTAMP WITH TIME ZONE,
    action_processed BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id, guild_id)
);

-- CREATE TABLE IF NOT EXISTS backups (
--     backup_id TEXT NOT NULL,
--     guild_id BIGINT NOT NULL,
--     created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
--     "image" JSONB NOT NULL,
--     PRIMARY KEY (backup_id, guild_id)
-- );

CREATE TABLE IF NOT EXISTS welcome_messages (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS goodbye_messages (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS boost_messages (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    template TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE SCHEMA IF NOT EXISTS feeds;

CREATE TABLE IF NOT EXISTS feeds.instagram (
    username TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    posts JSONB[] NOT NULL DEFAULT ARRAY[]::JSONB[],
    PRIMARY KEY (user_id, guild_id)
);

CREATE SCHEMA IF NOT EXISTS highlight;

CREATE TABLE IF NOT EXISTS highlight.words (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    keyword CITEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id, keyword)
);

CREATE TABLE IF NOT EXISTS highlight.blacklist (
    user_id BIGINT NOT NULL,
    entity_id BIGINT NOT NULL,
    PRIMARY KEY (user_id, entity_id)
);

CREATE SCHEMA IF NOT EXISTS metrics;

CREATE TABLE IF NOT EXISTS metrics.names (
    user_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    pomelo BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS metrics.avatars (
    user_id BIGINT NOT NULL,
    asset TEXT NOT NULL,
    key TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, key)
);

CREATE SCHEMA IF NOT EXISTS lastfm;

CREATE TABLE IF NOT EXISTS lastfm.config (
    user_id BIGINT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    color BIGINT,
    reactions JSONB[] NOT NULL DEFAULT ARRAY[]::JSONB[]
);

CREATE TABLE IF NOT EXISTS lastfm.commands (
    user_id BIGINT NOT NULL,
    command TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lastfm.artists (
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    artist CITEXT NOT NULL,
    plays BIGINT NOT NULL,
    PRIMARY KEY (user_id, artist)
);

CREATE TABLE IF NOT EXISTS lastfm.albums (
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    artist CITEXT NOT NULL,
    album CITEXT NOT NULL,
    plays BIGINT NOT NULL,
    PRIMARY KEY (user_id, artist, album)
);

CREATE TABLE IF NOT EXISTS lastfm.tracks (
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    artist CITEXT NOT NULL,
    track CITEXT NOT NULL,
    plays BIGINT NOT NULL,
    PRIMARY KEY (user_id, artist, track)
);

CREATE TABLE IF NOT EXISTS lastfm.crowns (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    username TEXT NOT NULL,
    artist CITEXT NOT NULL,
    plays BIGINT NOT NULL,
    PRIMARY KEY (guild_id, artist)
);

CREATE OR REPLACE FUNCTION NEXT_CASE(BIGINT) RETURNS BIGINT
    LANGUAGE plpgsql
    AS $$
DECLARE
    next_id BIGINT;
BEGIN
    SELECT MAX(id) INTO next_id FROM cases WHERE guild_id = $1;
    IF next_id IS NULL THEN RETURN 1; END IF;
    RETURN next_id + 1;
END;
$$;

-- CREATE OR REPLACE FUNCTION limit_backups()
-- RETURNS TRIGGER AS $$
-- DECLARE
--   backup_count INTEGER;
-- BEGIN
--   SELECT COUNT(*) INTO backup_count
--   FROM backups
--   WHERE guild_id = NEW.guild_id;
  
--   IF backup_count > 10 THEN
--     DELETE FROM backups
--     WHERE (backup_id, guild_id) IN (
--       SELECT backup_id, guild_id
--       FROM backups
--       WHERE guild_id = NEW.guild_id
--       ORDER BY created_at ASC
--       LIMIT 1
--     );
--   END IF;
  
--   RETURN NEW;
-- END;
-- $$ LANGUAGE plpgsql;

-- CREATE OR REPLACE TRIGGER limit_backups_trigger
-- BEFORE INSERT ON backups
-- FOR EACH ROW
-- EXECUTE FUNCTION limit_backups();