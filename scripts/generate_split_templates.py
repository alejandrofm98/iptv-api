#!/usr/bin/env python3
"""
Genera templates M3U separados por tipo de contenido desde un template existente.
"""
import os
import re
from pathlib import Path

def split_template(input_path: str, output_dir: str):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    live_lines = ['#EXTM3U']
    movie_lines = ['#EXTM3U']
    series_lines = ['#EXTM3U']
    
    counts = {'live': 0, 'movie': 0, 'series': 0}
    
    current_extinf = None
    
    for line in lines:
        line = line.rstrip('\r\n\t ')
        
        if line.startswith('#EXTINF:'):
            current_extinf = line
            continue
        
        if not line or line.startswith('#') and not line.startswith('#EXTINF:'):
            continue
        
        content_type = None
        
        if '/series/' in line:
            content_type = 'series'
        elif '/movie/' in line:
            content_type = 'movie'
        elif '/live/' in line or ('{{DOMAIN}}/{{USERNAME}}/{{PASSWORD}}/' in line and '/series/' not in line and '/movie/' not in line):
            content_type = 'live'
            if '/live/' not in line and '{{DOMAIN}}/{{USERNAME}}/{{PASSWORD}}/' in line:
                line = line.replace('{{DOMAIN}}/{{USERNAME}}/{{PASSWORD}}/', '{{DOMAIN}}/live/{{USERNAME}}/{{PASSWORD}}/')
        
        if content_type and current_extinf:
            counts[content_type] += 1
            if content_type == 'live':
                live_lines.append(current_extinf)
                live_lines.append(line)
            elif content_type == 'movie':
                movie_lines.append(current_extinf)
                movie_lines.append(line)
            elif content_type == 'series':
                series_lines.append(current_extinf)
                series_lines.append(line)
        
        current_extinf = None
    
    def write_file(lines, filename):
        path = os.path.join(output_dir, filename)
        content = '\n'.join(lines)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        size_mb = len(content.encode('utf-8')) / 1024 / 1024
        print(f"  ✅ {filename}: {size_mb:.2f} MB")
        return path
    
    print("💾 Generando templates separados:")
    write_file(live_lines, 'playlist_template_live.m3u')
    write_file(movie_lines, 'playlist_template_movie.m3u')
    write_file(series_lines, 'playlist_template_series.m3u')
    
    print(f"\n📊 Conteo por tipo:")
    for t, c in counts.items():
        print(f"  {t}: {c:,} items")
    
    return counts

if __name__ == '__main__':
    project_root = Path(__file__).parent.parent
    m3u_dir = str(project_root / 'data' / 'm3u')
    input_file = os.path.join(m3u_dir, 'playlist_template.m3u')
    
    if not os.path.exists(input_file):
        print(f"❌ No se encontró: {input_file}")
        exit(1)
    
    print(f"📁 Procesando: {input_file}")
    split_template(input_file, m3u_dir)
    print("\n✅ Done!")
