-- ============================================
-- IPTV API - Script SQL para Supabase
-- ============================================
-- Ejecutar este script en el SQL Editor de Supabase

-- ============================================
-- 1. Tabla de usuarios IPTV
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    max_connections INT DEFAULT 2 CHECK (max_connections >= 1 AND max_connections <= 10),
    is_active BOOLEAN DEFAULT true,
    role VARCHAR(20) DEFAULT 'user',
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Índices para usuarios
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);
CREATE INDEX IF NOT EXISTS idx_users_expires_at ON users(expires_at);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)

-- Trigger para actualizar updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 2. Tabla de sesiones/dispositivos activos
-- ============================================
CREATE TABLE IF NOT EXISTS active_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(64) NOT NULL,
    device_name VARCHAR(100),
    device_type VARCHAR(20) DEFAULT 'unknown',
    ip_address VARCHAR(45),
    user_agent TEXT,
    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, device_id)
);

-- Índices para sesiones
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON active_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_device_id ON active_sessions(device_id);
CREATE INDEX IF NOT EXISTS idx_sessions_last_activity ON active_sessions(last_activity);

-- ============================================
-- 3. Función para truncar tablas (optimización)
-- ============================================
CREATE OR REPLACE FUNCTION truncate_table(table_name TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE 'TRUNCATE TABLE ' || quote_ident(table_name) || ' RESTART IDENTITY CASCADE';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- 4. Función para limpiar sesiones inactivas
-- ============================================
CREATE OR REPLACE FUNCTION cleanup_inactive_sessions(timeout_minutes INT DEFAULT 30)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM active_sessions
    WHERE last_activity < NOW() - (timeout_minutes || ' minutes')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 5. Vista de usuarios con dispositivos activos
-- ============================================
CREATE OR REPLACE VIEW users_with_devices AS
SELECT
    u.id,
    u.username,
    u.max_connections,
    u.is_active,
    u.expires_at,
    u.created_at,
    COUNT(s.id) as active_devices,
    ARRAY_AGG(
        jsonb_build_object(
            'device_id', s.device_id,
            'device_name', s.device_name,
            'device_type', s.device_type,
            'ip_address', s.ip_address,
            'last_activity', s.last_activity
        )
    ) FILTER (WHERE s.id IS NOT NULL) as devices
FROM users u
LEFT JOIN active_sessions s ON u.id = s.user_id
GROUP BY u.id, u.username, u.max_connections, u.is_active, u.expires_at, u.created_at;

-- ============================================
-- 6. Tabla de configuración
-- ============================================

CREATE TABLE config (
  key TEXT PRIMARY KEY,
  value TEXT,
  description TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================
-- 7. Tabla de replays UFC
-- ============================================
CREATE TABLE IF NOT EXISTS replays (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    source_site TEXT NOT NULL DEFAULT 'watch-wrestling.eu',
    source_id BIGINT UNIQUE,
    category TEXT,
    title TEXT NOT NULL,
    event_name TEXT,
    event_type TEXT,
    event_date DATE,
    published_at TIMESTAMP WITH TIME ZONE,
    modified_at TIMESTAMP WITH TIME ZONE,
    post_url TEXT NOT NULL,
    featured_image_url TEXT,
    excerpt TEXT,
    description TEXT,
    video_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    match_card JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_replays_source_site ON replays(source_site);
CREATE INDEX IF NOT EXISTS idx_replays_event_date ON replays(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_replays_published_at ON replays(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_replays_event_type ON replays(event_type);

DROP TRIGGER IF EXISTS update_replays_updated_at ON replays;
CREATE TRIGGER update_replays_updated_at
    BEFORE UPDATE ON replays
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
