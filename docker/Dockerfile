# ============================================
# IPTV API - Dockerfile
# ============================================
FROM python:3.12-slim

# Variables de entorno
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=true

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    git \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
# Copiamos desde el contexto raíz del proyecto
COPY src/iptv_api/ /app/iptv_api/
COPY resources/ /app/resources/

# Clonar iptv-db para migraciones automáticas
ARG IPTV_DB_REF=main
RUN git clone https://github.com/alejandrofm98/iptv-db.git /opt/iptv-db \
    && git -C /opt/iptv-db checkout "${IPTV_DB_REF}"

# Entrypoint con migración automática
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Crear directorios necesarios

# Puerto expuesto
EXPOSE 3010

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=5 \
    CMD curl -f http://localhost:3010/health || exit 1

# Comando por defecto
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "iptv_api.main:app", "--host", "0.0.0.0", "--port", "3010"]
