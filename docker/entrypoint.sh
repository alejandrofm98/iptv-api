#!/bin/bash
set -e

# Aplicar migraciones pendientes de iptv-db
cd /opt/iptv-db
git pull origin main 2>/dev/null || echo "iptv-db pull skipped, using built-in version"

python3 -m alembic upgrade head

# Volver al directorio de la aplicacion antes de arrancar uvicorn
cd /app

exec "$@"
