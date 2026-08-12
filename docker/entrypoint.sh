#!/bin/bash
set -e

# Aplicar migraciones pendientes de iptv-db
cd /opt/iptv-db

python3 -m alembic upgrade head

# Volver al directorio de la aplicacion antes de arrancar uvicorn
cd /app

exec "$@"
