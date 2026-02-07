"""
Script de sincronización IPTV con Supabase
Descarga, parsea y sincroniza canales, películas y series
"""

import os
import requests
import time
import re
import traceback
from datetime import datetime
from pathlib import Path
from supabase import Client

# Importar configuración común
from utils.config import get_settings
import utils.constants as CONSTANTS

# ✨ Importar módulo de inserción optimizada
from services.bulk_insert import insert_bulk_optimized

# Cargar configuración
settings = get_settings()


def init_supabase() -> Client:
  """Inicializa el cliente de Supabase"""
  return settings.get_supabase_client()


def detectar_tipo_contenido(url, nombre):
  """
  Detecta si es canal, película o serie basándose en la URL y nombre
  Returns: 'channel', 'movie' o 'serie'
  """
  url_lower = url.lower()
  nombre_lower = nombre.lower()

  # Detectar series
  if CONSTANTS.URL_SERIES_PATH in url_lower or re.search(
      CONSTANTS.SERIES_PATTERN, nombre_lower
  ):
    return CONSTANTS.CONTENT_TYPE_SERIE

  # Detectar películas
  if CONSTANTS.URL_MOVIE_PATH in url_lower:
    return CONSTANTS.CONTENT_TYPE_MOVIE

  # Por defecto, es un canal de TV en vivo
  return CONSTANTS.CONTENT_TYPE_CHANNEL


def proxy_logo_url(logo_url: str, public_domain: str) -> str:
  """
  Convierte URLs de logos HTTP a HTTPS usando el proxy.
  
  Args:
      logo_url: URL original del logo (puede ser HTTP)
      public_domain: Dominio público del API (https://iptv.walerike.com)
  
  Returns:
      URL transformada usando el proxy HTTPS
  """
  if not logo_url:
    return CONSTANTS.DEFAULT_LOGO_URL
  
  # Si ya es HTTPS o es una URL local, dejarla como está
  if logo_url.startswith('https://') or logo_url.startswith('/'):
    return logo_url
  
  # Convertir HTTP a HTTPS usando el proxy (SIN codificar)
  # La API se encarga de decodificar si es necesario
  return f"{public_domain}/logo/{logo_url}"


def extraer_temporada_episodio(nombre):
  """
  Extrae temporada y episodio del nombre
  Ejemplos:
    - "NL - KING AND CONQUEROR S01 E01" -> ('01', '01')
    - "Serie S2 E10" -> ('2', '10')
  Returns: (temporada, episodio) o (None, None)
  """
  match = re.search(CONSTANTS.SERIES_PATTERN, nombre, re.IGNORECASE)
  if match:
    temporada = match.group(1).zfill(2)
    episodio = match.group(2).zfill(2)
    return (temporada, episodio)

  return (None, None)


def extraer_country(grupo):
  """
  Extrae el código de país del grupo
  Ejemplos:
    - "ES|DEPORTES" -> "ES"
    - "|AR| افلام اجنبي اكشن" -> "AR"
    - "NL| AMAZON PRIME" -> "NL"
  """
  if not grupo:
    return None

  match = re.search(CONSTANTS.COUNTRY_CODE_PATTERN, grupo)
  if match:
    return match.group(1)

  return None


def extraer_provider_id(url: str) -> str:
  """
  Extrae el provider_id de la URL del proveedor.

  Ejemplos:
    - http://PROVIDER_URL/USER/PASS/176861 → "176861"
    - http://PROVIDER_URL/series/USER/PASS/1306345.mkv → "1306345"
    - http://PROVIDER_URL/movie/USER/PASS/2001330.mkv → "2001330"
  """
  try:
    # Obtener la última parte de la URL (después del último /)
    last_part = url.rstrip('/').split('/')[-1]
    # Quitar extensión si existe (.mkv, .mp4, .ts, etc.)
    provider_id = last_part.split('.')[0]
    return provider_id
  except Exception:
    return ""


def procesar_item(item, idx, tipo):
  """Procesa un item (canal/movie/serie) según su tipo"""
  item_id = str(idx)

  # Extraer country del grupo
  country = extraer_country(item['group'])

  # Convertir URL del logo a HTTPS usando el proxy
  logo_url = proxy_logo_url(item['logo'], settings.public_domain)

  # Extraer provider_id de la URL
  provider_id = extraer_provider_id(item['url'])

  # Datos base comunes a todos los tipos
  data_base = {
    "id": item_id,
    "numero": idx,
    "nombre": item['name'],
    "logo": logo_url,
    "url": item['url'],
    "provider_id": provider_id,
    "grupo": item['group'],
    "country": country,
    "tvg_id": item.get('tvg_id', '')
  }

  # Si es serie, añadir temporada y episodio
  if tipo == CONSTANTS.CONTENT_TYPE_SERIE:
    temporada, episodio = extraer_temporada_episodio(item['name'])
    data_base['temporada'] = temporada
    data_base['episodio'] = episodio

  return data_base


def contar_registros_tabla(supabase, tabla):
  """Cuenta cuántos registros hay en una tabla de Supabase"""
  try:
    result = supabase.table(tabla).select('*', count='exact').limit(1).execute()
    return result.count if result.count is not None else 0
  except Exception as e:
    print(f"  ⚠️  Error al contar registros en '{tabla}': {e}")
    return 0


def limpiar_m3u_antiguos(m3u_dir: str, copias_mantener: int = None):
  """
  Elimina TODOS los archivos M3U anteriores antes de generar nuevos.
  Esto incluye playlist.m3u, playlist_template.m3u y todos los backups.
  """
  try:
    archivos_a_eliminar = []

    for filename in os.listdir(m3u_dir):
      # Eliminar TODOS los archivos .m3u sin excepciones
      if filename.endswith('.m3u'):
        filepath = os.path.join(m3u_dir, filename)
        if os.path.isfile(filepath):
          archivos_a_eliminar.append(filepath)

    if len(archivos_a_eliminar) == 0:
      print(f"  📂 Directorio limpio, no hay archivos M3U para eliminar")
      return

    # Eliminar todos los archivos
    eliminados = 0
    for filepath in archivos_a_eliminar:
      try:
        os.remove(filepath)
        eliminados += 1
      except Exception as e:
        print(f"  ⚠️  No se pudo eliminar {os.path.basename(filepath)}: {e}")

    print(f"  🗑️  Eliminados {eliminados} archivos M3U anteriores (limpieza total)")

  except Exception as e:
    print(f"  ⚠️  Error al limpiar archivos antiguos: {e}")


def crear_template_m3u(contenido_m3u: str) -> str:
  """
  Procesa el M3U original y crea una versión template con placeholders.
  Esto permite generar playlists personalizadas rápidamente sin regex en cada request.
  
  Args:
    contenido_m3u: Contenido del M3U original con credenciales del proveedor
    
  Returns:
    M3U con placeholders: {{USERNAME}} y {{PASSWORD}} en lugar de credenciales reales
  """
  lines = contenido_m3u.split('\n')
  processed_lines = []

  # Compilar regex patterns (solo una vez durante el sync)
  # IMPORTANTE: Soportar tanto .mkv como .mp4
  pattern_series = re.compile(r'http://PROVIDER_URL/series/[^/]+/[^/]+/(\d+)\.(mkv|mp4)$')
  pattern_movie = re.compile(r'http://PROVIDER_URL/movie/[^/]+/[^/]+/(\d+)\.(mkv|mp4)$')
  pattern_live = re.compile(r'http://PROVIDER_URL/[^/]+/[^/]+/(\d+)$')
  
  for line in lines:
    # Limpiar caracteres de control (\r de Windows, espacios al final)
    line = line.rstrip('\r\n\t ')
    original_line = line
    matched = False
    
    # Verificar series primero (más específico)
    if pattern_series.search(line):
      line = pattern_series.sub(r'{{DOMAIN}}/series/{{USERNAME}}/{{PASSWORD}}/\1.\2', line)
      matched = True
    # Verificar movies
    elif pattern_movie.search(line):
      line = pattern_movie.sub(r'{{DOMAIN}}/movie/{{USERNAME}}/{{PASSWORD}}/\1.\2', line)
      matched = True
    # Verificar live (sin subdirectorio /live/)
    elif pattern_live.search(line):
      # Live: sin subdirectorio, directo: dominio/user/pass/id
      line = pattern_live.sub(r'{{DOMAIN}}/{{USERNAME}}/{{PASSWORD}}/\1', line)
      matched = True
    
    processed_lines.append(line)
  
  return '\n'.join(processed_lines)


def guardar_m3u_local(contenido_m3u: str, m3u_dir: str = None):
  """
  Guarda el archivo M3U en el servidor local (accesible por Nginx)
  """
  # Detectar si estamos en Docker o local
  is_docker = (
      os.path.exists(CONSTANTS.DOCKER_ENV_PATH) or
      os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
  )

  if m3u_dir is None:
    if is_docker:
      m3u_dir = os.getenv(CONSTANTS.M3U_DIR_ENV, CONSTANTS.M3U_DIR_DOCKER)
    else:
      project_root = Path(__file__).parent.parent
      m3u_dir = os.getenv(
          CONSTANTS.M3U_DIR_ENV,
          str(project_root / CONSTANTS.M3U_DIR_LOCAL_DEFAULT)
      )

  try:
    print(f"📁 Preparando directorio: {m3u_dir}")
    os.makedirs(m3u_dir, exist_ok=True)

    # PRIMERO: Limpiar TODOS los archivos M3U anteriores
    print(f"  🧹 Limpiando archivos M3U anteriores...")
    limpiar_m3u_antiguos(m3u_dir)

    # Calcular tamaño
    file_bytes = contenido_m3u.encode('utf-8')
    size_bytes = len(file_bytes)
    size_kb = size_bytes / 1024
    size_mb = size_kb / 1024

    print(f"💾 Generando template M3U:")
    print(f"  📊 Tamaño: {size_mb:.2f} MB")
    print(f"  📁 Directorio: {m3u_dir}")

    # Crear versión template con placeholders para procesamiento rápido
    # Usando ATOMIC WRITE para evitar race conditions durante la lectura
    print(f"  🔧 Creando template con placeholders...")
    template_content = crear_template_m3u(contenido_m3u)
    path_template = os.path.join(m3u_dir, "playlist_template.m3u")
    path_template_tmp = os.path.join(m3u_dir, "playlist_template.m3u.tmp")
    
    # Paso 1: Escribir a archivo temporal
    with open(path_template_tmp, 'w', encoding='utf-8') as f:
      f.write(template_content)
    
    # Paso 2: Renombrar atómicamente (operación instantánea en filesystem)
    # Esto garantiza que el archivo nunca esté en estado parcial
    os.rename(path_template_tmp, path_template)
    
    print(f"    ✅ Template guardado (atomic write): playlist_template.m3u")

    # URL pública
    if is_docker:
      base_domain = os.getenv(
          CONSTANTS.PUBLIC_DOMAIN_ENV,
          CONSTANTS.PUBLIC_DOMAIN_DEFAULT_DOCKER
      )
    else:
      base_domain = os.getenv(
          CONSTANTS.PUBLIC_DOMAIN_ENV,
          CONSTANTS.PUBLIC_DOMAIN_DEFAULT_LOCAL
      )

    print(f"✅ Template M3U generado correctamente:")
    print(f"  📄 Archivo: playlist_template.m3u")
    print(f"  📊 Tamaño: {size_mb:.2f} MB ({size_bytes:,} bytes)")
    print(f"  📁 Ubicación: {path_template}")

    return {
      "template_path": path_template,
      "template_filename": "playlist_template.m3u",
      "size": size_bytes,
      "size_mb": size_mb
    }

  except Exception as e:
    print(f"❌ Error al guardar M3U localmente: {e}")
    traceback.print_exc()
    return None


def limpiar_tabla_optimizada(supabase: Client, tabla: str) -> bool:
  """
  Limpia una tabla de forma optimizada usando TRUNCATE o DELETE en lotes
  """
  print(f"  🗑️  Limpiando tabla '{tabla}'...")

  try:
    supabase.rpc('truncate_table', {'table_name': tabla}).execute()
    print(f"  ✅ Tabla '{tabla}' limpiada con TRUNCATE")
    return True
  except Exception:
    print(f"  ⚠️  TRUNCATE no disponible, usando DELETE en lotes...")

  deleted_count = 0
  attempt = 0

  while attempt < CONSTANTS.MAX_DELETE_ATTEMPTS:
    try:
      result = (
        supabase.table(tabla)
        .delete()
        .limit(CONSTANTS.DELETE_BATCH_LIMIT)
        .neq('id', '')
        .execute()
      )

      if not result.data or len(result.data) == 0:
        break

      deleted_count += len(result.data)

      if deleted_count % 10000 == 0:
        print(f"    🗑️  Eliminados {deleted_count:,} registros...")

      time.sleep(CONSTANTS.DELETE_BATCH_SLEEP)
      attempt += 1

    except Exception as delete_error:
      print(f"  ❌ Error en borrado: {delete_error}")
      return False

  print(
    f"  ✅ Tabla '{tabla}' limpiada ({deleted_count:,} registros eliminados)")
  return True


def parsear_m3u(m3u_content: str) -> list:
  """Parsea contenido M3U y retorna lista de items"""
  items_temp = []
  lines = m3u_content.split('\n')
  current_item = {}

  for line in lines:
    line = line.strip()

    if line.startswith(CONSTANTS.M3U_EXTINF_PREFIX):
      # Extraer información del item
      group = ''
      if CONSTANTS.M3U_GROUP_TITLE_ATTR in line:
        group = line.split(CONSTANTS.M3U_GROUP_TITLE_ATTR)[1].split('"')[0]

      name = line.split(',')[-1].strip() if ',' in line else 'Unknown'

      logo = ''
      if CONSTANTS.M3U_TVG_LOGO_ATTR in line:
        raw_logo = line.split(CONSTANTS.M3U_TVG_LOGO_ATTR)[1].split('"')[0]
        logo = proxy_logo_url(raw_logo, settings.public_domain)

      tvg_id = ''
      if CONSTANTS.M3U_TVG_ID_ATTR in line:
        tvg_id = line.split(CONSTANTS.M3U_TVG_ID_ATTR)[1].split('"')[0]

      current_item = {
        'name': name,
        'group': group,
        'logo': logo,
        'tvg_id': tvg_id
      }

    elif line and not line.startswith('#') and current_item:
      current_item['url'] = line
      items_temp.append(current_item.copy())
      current_item = {}

  return items_temp


def sync_to_supabase():
  """
  Sincroniza canales, películas y series a Supabase en tablas separadas.
  ✨ VERSIÓN OPTIMIZADA con inserción paralela
  """
  inicio_total = time.time()
  hora_inicio = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

  print("\n" + "=" * 70)
  print(f"🚀 INICIANDO SINCRONIZACIÓN IPTV")
  print("=" * 70)
  print(f"⏰ Hora de inicio: {hora_inicio}")
  print("=" * 70 + "\n")

  # Validar configuración
  if not settings.is_valid():
    print("❌ Error: Configuración incompleta")
    print(f"📋 Estado de configuración:\n{settings}")
    return

  print(f"📋 Configuración cargada:\n{settings}")

  url = settings.iptv_source_url

  if not url:
    print("❌ Error: URL de playlist IPTV no configurada")
    return

  try:
    print("\n📥 FASE 1: Descargando playlist M3U...")
    inicio_descarga = time.time()

    response = requests.get(url, timeout=CONSTANTS.PLAYLIST_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    m3u_content = response.text

    fin_descarga = time.time()
    duracion_descarga = fin_descarga - inicio_descarga

    print(f"✅ Playlist descargada: {len(m3u_content):,} caracteres")
    print(f"  ⏱️  Tiempo de descarga: {duracion_descarga:.2f}s")

  except Exception as e:
    print(f"❌ Error de conexión: {e}")
    return

  # Inicializar Supabase
  try:
    supabase = init_supabase()
    print("✅ Conectado a Supabase")
  except Exception as e:
    print(f"❌ Error al conectar con Supabase: {e}")
    return

  # Guardar archivo M3U
  print("\n" + "=" * 60)
  m3u_info = guardar_m3u_local(m3u_content)
  print("=" * 60 + "\n")

  if not m3u_info:
    print(
      "⚠️  No se pudo guardar el archivo M3U, pero continuaremos con Supabase")

  # Parsear contenido
  print("\n📺 FASE 2: Parseando contenido M3U...")
  inicio_parseo = time.time()

  items_temp = parsear_m3u(m3u_content)

  fin_parseo = time.time()
  duracion_parseo = fin_parseo - inicio_parseo

  print(f"✅ Parseados {len(items_temp):,} items en total")
  print(f"  ⏱️  Tiempo de parseo: {duracion_parseo:.2f}s")

  # Clasificar items por tipo
  channels = []
  movies = []
  series = []

  stats = {
    'channels': {'total': 0, 'con_logo': 0, 'sin_logo': 0},
    'movies': {'total': 0, 'con_logo': 0, 'sin_logo': 0},
    'series': {'total': 0, 'con_logo': 0, 'sin_logo': 0}
  }

  print(f"\n🔍 FASE 3: Clasificando contenido por tipo...")
  inicio_clasificacion = time.time()

  idx_channel = 1
  idx_movie = 1
  idx_serie = 1

  for item in items_temp:
    tipo = detectar_tipo_contenido(item['url'], item['name'])

    if tipo == CONSTANTS.CONTENT_TYPE_CHANNEL:
      item_data = procesar_item(item, idx_channel, tipo)
      channels.append(item_data)
      idx_channel += 1
      stats['channels']['total'] += 1
      if item['logo']:
        stats['channels']['con_logo'] += 1
      else:
        stats['channels']['sin_logo'] += 1

    elif tipo == CONSTANTS.CONTENT_TYPE_MOVIE:
      item_data = procesar_item(item, idx_movie, tipo)
      movies.append(item_data)
      idx_movie += 1
      stats['movies']['total'] += 1
      if item['logo']:
        stats['movies']['con_logo'] += 1
      else:
        stats['movies']['sin_logo'] += 1

    elif tipo == CONSTANTS.CONTENT_TYPE_SERIE:
      item_data = procesar_item(item, idx_serie, tipo)
      series.append(item_data)
      idx_serie += 1
      stats['series']['total'] += 1
      if item['logo']:
        stats['series']['con_logo'] += 1
      else:
        stats['series']['sin_logo'] += 1

  fin_clasificacion = time.time()
  duracion_clasificacion = fin_clasificacion - inicio_clasificacion

  print(f"✅ Clasificación completada en {duracion_clasificacion:.2f}s")
  print("\n" + "=" * 50)
  print(f"📊 Resumen de clasificación:")
  print(f"  📺 Canales: {stats['channels']['total']:,}")
  print(f"    - Con logo: {stats['channels']['con_logo']:,}")
  print(f"    - Sin logo: {stats['channels']['sin_logo']:,}")
  print(f"  🎬 Películas: {stats['movies']['total']:,}")
  print(f"    - Con logo: {stats['movies']['con_logo']:,}")
  print(f"    - Sin logo: {stats['movies']['sin_logo']:,}")
  print(f"  📺 Series: {stats['series']['total']:,}")
  print(f"    - Con logo: {stats['series']['con_logo']:,}")
  print(f"    - Sin logo: {stats['series']['sin_logo']:,}")

  if m3u_info:
    print(f"  📄 Template M3U:")
    print(
      f"    - Tamaño: {m3u_info['size']:,} bytes ({m3u_info['size_mb']:.2f} MB)")
    print(f"    - Ubicación: {m3u_info['template_path']}")

  # Verificar estado de la base de datos
  print("\n🔍 Verificando estado de la base de datos...")
  count_channels_db = contar_registros_tabla(supabase, CONSTANTS.CHANNELS_TABLE)
  count_movies_db = contar_registros_tabla(supabase, CONSTANTS.MOVIES_TABLE)
  count_series_db = contar_registros_tabla(supabase, CONSTANTS.SERIES_TABLE)

  print(f"  📊 Estado actual en BD:")
  print(f"    - Canales: {count_channels_db:,}")
  print(f"    - Películas: {count_movies_db:,}")
  print(f"    - Series: {count_series_db:,}")
  print(f"  📊 Nuevos datos a insertar:")
  print(f"    - Canales: {len(channels):,}")
  print(f"    - Películas: {len(movies):,}")
  print(f"    - Series: {len(series):,}")

  # Verificar si coinciden los números
  channels_match = count_channels_db == len(channels)
  movies_match = count_movies_db == len(movies)
  series_match = count_series_db == len(series)

  if channels_match and movies_match and series_match:
    fin_total = time.time()
    duracion_total = fin_total - inicio_total
    hora_fin = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("\n✅ ¡Los datos ya están sincronizados!")
    print("  ℹ️  Las cantidades coinciden exactamente.")
    print("  ⏭️  Saltando sincronización para ahorrar tiempo y recursos.")

    # Actualizar metadata
    try:
      metadata = {
        "ultima_actualizacion": datetime.now().isoformat(),
        "total_canales": len(channels),
        "total_movies": len(movies),
        "total_series": len(series),
        "m3u_size": m3u_info['size'] if m3u_info else None,
        "m3u_size_mb": m3u_info['size_mb'] if m3u_info else None,
        "channels_con_logo": stats['channels']['con_logo'],
        "channels_sin_logo": stats['channels']['sin_logo'],
        "movies_con_logo": stats['movies']['con_logo'],
        "movies_sin_logo": stats['movies']['sin_logo'],
        "series_con_logo": stats['series']['con_logo'],
        "series_sin_logo": stats['series']['sin_logo']
      }

      supabase.table(CONSTANTS.SYNC_METADATA_TABLE).upsert({
        "id": CONSTANTS.SYNC_METADATA_ID,
        **metadata
      }).execute()

      print("  ✅ Metadata actualizada")
    except Exception:
      pass

    print(f"\n⏱️  TIEMPOS:")
    print(f"  🕐 Inicio: {hora_inicio}")
    print(f"  🕐 Fin: {hora_fin}")
    print(f"  ⏱️  Duración: {duracion_total:.2f}s")
    print(
      f"\n🌐 Archivo M3U template disponible en: {m3u_info['template_path'] if m3u_info else 'N/A'}")
    return

  # Si no coinciden, mostrar diferencias
  print("\n⚠️  Diferencias detectadas:")
  if not channels_match:
    diff = len(channels) - count_channels_db
    print(f"  📺 Canales: {diff:+,} ({count_channels_db:,} → {len(channels):,})")
  if not movies_match:
    diff = len(movies) - count_movies_db
    print(f"  🎬 Películas: {diff:+,} ({count_movies_db:,} → {len(movies):,})")
  if not series_match:
    diff = len(series) - count_series_db
    print(f"  📺 Series: {diff:+,} ({count_series_db:,} → {len(series):,})")

  # Guardar en Supabase
  try:
    print("\n💾 FASE 4: Guardando contenido en Supabase (MODO OPTIMIZADO)...")
    print("=" * 70)
    inicio_insercion = time.time()

    total_items = len(channels) + len(movies) + len(series)
    if total_items == 0:
      print("❌ No hay contenido para insertar.")
      return

    print(f"✅ Validado: {total_items:,} items listos para insertar")

    tiempo_channels = 0
    tiempo_movies = 0
    tiempo_series = 0

    # 1. Canales
    if not channels_match and len(channels) > 0:
      inicio_channels = time.time()
      limpiar_tabla_optimizada(supabase, CONSTANTS.CHANNELS_TABLE)

      stats_channels = insert_bulk_optimized(
          supabase_client=supabase,
          table_name=CONSTANTS.CHANNELS_TABLE,
          data=channels,
          batch_size=CONSTANTS.SUPABASE_DEFAULT_BATCH_SIZE,
          max_workers=CONSTANTS.SUPABASE_DEFAULT_MAX_WORKERS
      )

      tiempo_channels = time.time() - inicio_channels
    else:
      print(f"  ⏭️  Canales: sin cambios ({count_channels_db:,} registros)")
      stats_channels = None

    # 2. Películas
    if not movies_match and len(movies) > 0:
      inicio_movies = time.time()
      limpiar_tabla_optimizada(supabase, CONSTANTS.MOVIES_TABLE)

      stats_movies = insert_bulk_optimized(
          supabase_client=supabase,
          table_name=CONSTANTS.MOVIES_TABLE,
          data=movies,
          batch_size=CONSTANTS.SUPABASE_DEFAULT_BATCH_SIZE,
          max_workers=CONSTANTS.SUPABASE_DEFAULT_MAX_WORKERS
      )

      tiempo_movies = time.time() - inicio_movies
    else:
      print(f"  ⏭️  Películas: sin cambios ({count_movies_db:,} registros)")
      stats_movies = None

    # 3. Series
    if not series_match and len(series) > 0:
      inicio_series = time.time()
      limpiar_tabla_optimizada(supabase, CONSTANTS.SERIES_TABLE)

      print(f"\n  🚀 Insertando {len(series):,} series con:")
      print(f"    📦 Batch size: {CONSTANTS.SUPABASE_DEFAULT_BATCH_SIZE:,}")
      print(f"    👷 Workers: {CONSTANTS.SUPABASE_DEFAULT_MAX_WORKERS}")

      stats_series = insert_bulk_optimized(
          supabase_client=supabase,
          table_name=CONSTANTS.SERIES_TABLE,
          data=series,
          batch_size=CONSTANTS.SUPABASE_DEFAULT_BATCH_SIZE,
          max_workers=CONSTANTS.SUPABASE_DEFAULT_MAX_WORKERS
      )

      tiempo_series = time.time() - inicio_series
    else:
      print(f"  ⏭️  Series: sin cambios ({count_series_db:,} registros)")
      stats_series = None

    fin_insercion = time.time()
    duracion_insercion = fin_insercion - inicio_insercion

    # Guardar metadata
    metadata = {
      "ultima_actualizacion": datetime.now().isoformat(),
      "total_canales": stats_channels.inserted_records if stats_channels else count_channels_db,
      "total_movies": stats_movies.inserted_records if stats_movies else count_movies_db,
      "total_series": stats_series.inserted_records if stats_series else count_series_db,
      "m3u_template_path": m3u_info['template_path'] if m3u_info else None,
      "m3u_template_filename": m3u_info['template_filename'] if m3u_info else None,
      "m3u_size": m3u_info['size'] if m3u_info else None,
      "m3u_size_mb": m3u_info['size_mb'] if m3u_info else None,
      "channels_con_logo": stats['channels']['con_logo'],
      "channels_sin_logo": stats['channels']['sin_logo'],
      "movies_con_logo": stats['movies']['con_logo'],
      "movies_sin_logo": stats['movies']['sin_logo'],
      "series_con_logo": stats['series']['con_logo'],
      "series_sin_logo": stats['series']['sin_logo']
    }

    try:
      supabase.table(CONSTANTS.SYNC_METADATA_TABLE).upsert({
        "id": CONSTANTS.SYNC_METADATA_ID,
        **metadata
      }).execute()
    except Exception:
      pass

    # Tiempos finales
    fin_total = time.time()
    duracion_total = fin_total - inicio_total
    hora_fin = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    horas = int(duracion_total // 3600)
    minutos = int((duracion_total % 3600) // 60)
    segundos = int(duracion_total % 60)

    duracion_formateada = []
    if horas > 0:
      duracion_formateada.append(f"{horas}h")
    if minutos > 0:
      duracion_formateada.append(f"{minutos}m")
    duracion_formateada.append(f"{segundos}s")
    duracion_str = " ".join(duracion_formateada)

    print("\n" + "=" * 70)
    print(f"✅ ¡SINCRONIZACIÓN COMPLETADA CON ÉXITO!")
    print("=" * 70)
    print(f"📊 RESUMEN DE DATOS:")
    print(f"  📺 Canales:    {metadata['total_canales']:>10,}")
    print(f"  🎬 Películas:  {metadata['total_movies']:>10,}")
    print(f"  📺 Series:     {metadata['total_series']:>10,}")
    print(f"  {'─' * 30}")
    print(f"  📊 TOTAL:      {total_items:>10,} items")
    print()
    print(f"⏱️  TIEMPOS:")
    print(f"  🕐 Inicio:     {hora_inicio}")
    print(f"  🕐 Fin:        {hora_fin}")
    print(f"  ⏱️  Duración:   {duracion_str} ({duracion_total:.2f}s)")
    print()
    print(f"⏱️  TIEMPOS POR FASE:")
    print(f"  📥 Descarga:        {duracion_descarga:>8.2f}s")
    print(f"  📺 Parseo:          {duracion_parseo:>8.2f}s")
    print(f"  🔍 Clasificación:   {duracion_clasificacion:>8.2f}s")
    print(f"  💾 Inserción BD:    {duracion_insercion:>8.2f}s")

    if tiempo_channels > 0 or tiempo_movies > 0 or tiempo_series > 0:
      print()
      print(f"⏱️  TIEMPOS POR TABLA:")
      if tiempo_channels > 0:
        print(
          f"  📺 Canales:    {tiempo_channels:>8.2f}s ({len(channels):,} registros)")
      if tiempo_movies > 0:
        print(
          f"  🎬 Películas:  {tiempo_movies:>8.2f}s ({len(movies):,} registros)")
      if tiempo_series > 0:
        print(
          f"  📺 Series:     {tiempo_series:>8.2f}s ({len(series):,} registros)")

    if duracion_total > 0:
      velocidad = total_items / duracion_total
      print()
      print(f"  🚀 Velocidad promedio: {velocidad:.0f} items/segundo")

    print()
    print(f"🌐 Archivo M3U template disponible en:")
    print(f"  📄 {m3u_info['template_filename'] if m3u_info else 'N/A'}")
    print(f"  📁 {m3u_info['template_path'] if m3u_info else 'N/A'}")
    print("=" * 70 + "\n")

  except Exception as e:
    fin_total = time.time()
    duracion_total = fin_total - inicio_total
    hora_fin = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n❌ Error al guardar en Supabase: {e}")
    print(f"\n⏱️  Tiempos hasta el error:")
    print(f"  Inicio: {hora_inicio}")
    print(f"  Fin: {hora_fin}")
    print(f"  Duración: {duracion_total:.2f}s")
    traceback.print_exc()


if __name__ == "__main__":
  sync_to_supabase()