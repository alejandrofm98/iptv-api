#!/bin/sh
# ============================================
# Entrypoint para sync-scheduler
# Ejecuta sync inmediatamente al arrancar, luego inicia supercronic
# ============================================

# Asegurar que Python encuentre los módulos en /app
export PYTHONPATH=/app:$PYTHONPATH

echo "🚀 Iniciando IPTV Sync Scheduler..."
echo "📅 $(date)"
echo "📂 Directorio de trabajo: $(pwd)"
echo "🔧 PYTHONPATH: $PYTHONPATH"

# Ejecutar sincronización inmediata
echo ""
echo "⚡ Ejecutando sincronización inicial..."
cd /app && python scripts/sync_iptv.py

# Verificar si el sync tuvo éxito
if [ $? -eq 0 ]; then
    echo "✅ Sincronización inicial completada exitosamente"
else
    echo "⚠️  La sincronización inicial tuvo errores, pero continuamos..."
fi

echo ""
echo "⏰ Iniciando scheduler con supercronic (cada 2 horas)..."
echo "   Próximas ejecuciones: 00:00, 02:00, 04:00, 06:00, etc."
echo ""

# Iniciar supercronic con el crontab
exec supercronic /app/crontab
