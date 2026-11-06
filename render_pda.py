import os
import shutil
import json
import math
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw
import subprocess
import sys

# Desactivar límite de tamaño de imagen en Pillow
Image.MAX_IMAGE_PIXELS = None

# ==========================================
# 1. DIMENSIONES DEL MAPA (4K TOTAL: 2K JUGABLE + 2K BORDE)
# ==========================================
# - Área jugable (Playable)       : 2048 x 2048 px (2K centrado, representando los 4096 m)
# - Margen exterior (Borde BG)    : 1024 px por lado (2048 px en total, representando 2048 m a cada lado)
# - Lienzo total (Canvas Total)   : 4096 x 4096 px (4K, representando los 8192 m de canvas)
TAMANO_JUGABLE = 2048
TAMANO_BORDE = 1024
TAMANO_TOTAL = 4096     # 1024 + 2048 + 1024 = 4096 px

PLAYABLE_M = 4096.0     # 4096 metros del área jugable (mapa x4)
HALF_M = PLAYABLE_M / 2.0

# Ancla de proyección geográfica (por defecto de map_layout.py: Ojuelos de Jalisco, Jalisco, México)
LAT_CENTER = 21.86529000
LON_CENTER = -101.59351000
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))

try:
    import map_layout
    LAT_CENTER = getattr(map_layout, "LAT_CENTER", LAT_CENTER)
    LON_CENTER = getattr(map_layout, "LON_CENTER", LON_CENTER)
    PLAYABLE_M = getattr(map_layout, "PLAYABLE_M", PLAYABLE_M)
    HALF_M = PLAYABLE_M / 2.0
    M_PER_DEG = getattr(map_layout, "M_PER_DEG", M_PER_DEG)
    M_PER_DEG_LON = getattr(map_layout, "M_PER_DEG_LON", M_PER_DEG * math.cos(math.radians(LAT_CENTER)))
except Exception as e:
    print(f"Nota: Usando valores de proyección por defecto ({e})")

# ==========================================
# 2. CONFIGURACIÓN DE COLORES Y ESTILOS PDA
# ==========================================
COLOR_BORDE_BG = (28, 42, 24)               # Fondo exterior (Montañas / Fuera de límite)
COLOR_PASTO = (108, 142, 86)                # Pasto base jugable (#6C8E56)
COLOR_CAMPO = (130, 158, 100)               # Campos de cultivo (#829E64)
COLOR_CAMPO_BORDE = (94, 120, 72)           # Bordes de campos (#5E7848)
COLOR_BOSQUE = (40, 72, 32)                 # Bosques caducifolios / rompevientos (#284820)
COLOR_BOSQUE_BORDE = (28, 54, 22)           # Bordes de bosques
COLOR_BOSQUE_CONIFERA = (26, 54, 26)        # Bosque de coníferas / Colina Espiral (#1A361A)
COLOR_BOSQUE_CONIFERA_BORDE = (18, 38, 18)  # Borde coníferas
COLOR_AGUA = (52, 108, 148)                 # Lago Valle Bonito (#346C94)
COLOR_AGUA_BORDE = (36, 80, 114)            # Bordes de agua
COLOR_GRANJA = (140, 122, 96)               # Granjas y corrales (#8C7A60)
COLOR_GRANJA_BORDE = (108, 92, 72)
COLOR_PUEBLO = (124, 128, 134)              # Manzanas urbanas Pueblo Valle Bonito (#7C8086)
COLOR_PUEBLO_BORDE = (94, 98, 104)
COLOR_INDUSTRIAL = (112, 118, 126)          # Zona Industrial Norte (#70767E)
COLOR_INDUSTRIAL_BORDE = (82, 88, 96)
COLOR_ASFALTO_PRIMARIO = (50, 52, 58)       # Rutas Principales Norte y Este (#32343A)
COLOR_ASFALTO_PRIMARIO_BORDE = (30, 32, 36)
COLOR_ASFALTO_SECUNDARIO = (66, 70, 78)     # Calles secundarias del pueblo (#42464E)
COLOR_ASFALTO_SECUNDARIO_BORDE = (44, 46, 52)
COLOR_TIERRA = (156, 132, 98)               # Terracerías agrícolas de tierra (#9C8462)
COLOR_TIERRA_BORDE = (118, 98, 70)
COLOR_PUENTE = (180, 50, 50)                # Puentes (#B43232)
COLOR_PUENTE_BORDE = (130, 35, 35)
COLOR_LIMITE_JUGABLE = (255, 255, 255)      # Línea límite del área jugable 2K

# ==========================================
# 3. RUTAS DE ARCHIVOS Y DETECCIÓN
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

MAPA_CANDIDATOS = [
    os.path.join(SCRIPT_DIR, "FS25_Valle_Bonito_x4"),
    os.path.join(SCRIPT_DIR, "FS25_Granja_bonita"),
]
MAPA_DIR = next((c for c in MAPA_CANDIDATOS if os.path.isdir(c)), MAPA_CANDIDATOS[0])

MAP_DIR = os.path.join(MAPA_DIR, "map")
TEXTURES_JSON = os.path.join(MAPA_DIR, "info_layers", "textures.json")
BG_TEXTURE_PATH = os.path.join(MAPA_DIR, "assets", "background", "background_texture.png")

OSM_CANDIDATOS = [
    os.path.join(SCRIPT_DIR, "osm_generator", "map.osm"),
    os.path.join(SCRIPT_DIR, "input", "map.osm"),
    os.path.join(SCRIPT_DIR, "osm_generator", "custom.osm"),
    os.path.join(SCRIPT_DIR, "input", "custom_osm.osm"),
    os.path.join(MAPA_DIR, "custom_osm.osm"),
]

OSM_PATH = next((c for c in OSM_CANDIDATOS if os.path.exists(c)), None)
if not OSM_PATH:
    raise FileNotFoundError("No se encontró ningún archivo OSM en las rutas esperadas.")

TEXCONV_CANDIDATOS = [
    r"C:\Users\david\OneDrive\Desktop\Map4FS\executables\texconv.exe",
    os.path.join(SCRIPT_DIR, "texconv.exe"),
    shutil.which("texconv.exe"),
]

RUTA_TEXCONV = next((c for c in TEXCONV_CANDIDATOS if c and os.path.exists(c)), None)

OVERVIEW_DDS = os.path.join(MAP_DIR, "overview.dds")
OVERVIEW_PNG = os.path.join(MAP_DIR, "overview.png")
BACKUP_DDS = os.path.join(MAP_DIR, "overview_orig.dds")

UI_DIR = os.path.join(MAP_DIR, "textures", "ui")
UI_OVERVIEW_DDS = os.path.join(UI_DIR, "overview.dds")
UI_BACKUP_DDS = os.path.join(UI_DIR, "overview_orig.dds")

PREVIEW_PNG = os.path.join(MAPA_DIR, "preview.png")
PREVIEW_DDS = os.path.join(MAPA_DIR, "preview.dds")
UI_PREVIEW_DDS = os.path.join(UI_DIR, "preview.dds")

print("=" * 65)
print("GENERADOR DE PDA PARA FS25 BASADO EN OPENSTREETMAP (OSM)")
print("=" * 65)
print(f"Lienzo total        : {TAMANO_TOTAL} x {TAMANO_TOTAL} px (4K)")
print(f"Área jugable        : {TAMANO_JUGABLE} x {TAMANO_JUGABLE} px (centrada, {PLAYABLE_M:.0f}m)")
print(f"Margen exterior     : {TAMANO_BORDE} px por lado (2048m por lado)")
print(f"Directorio del mapa : {MAPA_DIR}")
print(f"Archivo OSM         : {OSM_PATH}")
print(f"Ruta texconv.exe    : {RUTA_TEXCONV}")
print(f"Destino overview.dds: {OVERVIEW_DDS}")

# Respaldos originales
if os.path.exists(OVERVIEW_DDS) and not os.path.exists(BACKUP_DDS):
    print(f"Creando respaldo -> {BACKUP_DDS}")
    shutil.copy2(OVERVIEW_DDS, BACKUP_DDS)

if os.path.exists(UI_OVERVIEW_DDS) and not os.path.exists(UI_BACKUP_DDS):
    print(f"Creando respaldo -> {UI_BACKUP_DDS}")
    shutil.copy2(UI_OVERVIEW_DDS, UI_BACKUP_DDS)

# ==========================================
# 4. CARGA DE DATOS VECTORIALES (OSM + JSON)
# ==========================================
tex_data = {}
if os.path.exists(TEXTURES_JSON):
    print(f"Cargando capas complementarias de {TEXTURES_JSON}...")
    try:
        with open(TEXTURES_JSON, 'r', encoding='utf-8') as f:
            tex_data = json.load(f)
    except Exception as e:
        print(f"Aviso al cargar {TEXTURES_JSON}: {e}")

print("Leyendo y parseando archivo OSM...")
tree = ET.parse(OSM_PATH)
root = tree.getroot()

nodes = {}
for node in root.findall('node'):
    nid = node.get('id')
    lat = float(node.get('lat'))
    lon = float(node.get('lon'))
    nodes[nid] = (lat, lon)

def global_to_canvas_px(lat, lon, size_total=TAMANO_TOTAL):
    """Convierte lat/lon geográficas al lienzo total del PDA."""
    escala = size_total / 4096.0
    borde = int(round(1024.0 * escala))
    jugable = int(round(2048.0 * escala))
    x_m = HALF_M + (lon - LON_CENTER) * M_PER_DEG_LON
    y_m = HALF_M - (lat - LAT_CENTER) * M_PER_DEG
    px = borde + (x_m / PLAYABLE_M) * jugable
    py = borde + (y_m / PLAYABLE_M) * jugable
    return px, py

def local_m_to_canvas_px(x_m, y_m, size_total=TAMANO_TOTAL):
    """Convierte metros locales [0, 4096] al lienzo total del PDA."""
    escala = size_total / 4096.0
    borde = int(round(1024.0 * escala))
    jugable = int(round(2048.0 * escala))
    px = borde + (x_m / PLAYABLE_M) * jugable
    py = borde + (y_m / PLAYABLE_M) * jugable
    return px, py

# Clasificar ways del OSM para renderizado estratificado
osm_water = []
osm_islands = []
osm_farmland = []
osm_farmyard = []
osm_wood = []
osm_highways_track = []
osm_highways_sec = []
osm_highways_prim = []
osm_bridges = []

for way in root.findall('way'):
    tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
    nd_refs = [nd.get('ref') for nd in way.findall('nd') if nd.get('ref') in nodes]
    if len(nd_refs) < 2:
        continue
    pts = [nodes[ref] for ref in nd_refs]
    item = {'id': way.get('id'), 'tags': tags, 'pts': pts}

    name = tags.get('name', '')
    if 'Isla' in name:
        osm_islands.append(item)
    elif tags.get('natural') == 'water' or 'water' in tags:
        osm_water.append(item)
    elif tags.get('natural') == 'wood' or tags.get('landuse') == 'forest':
        osm_wood.append(item)
    elif tags.get('landuse') == 'farmland':
        osm_farmland.append(item)
    elif tags.get('landuse') == 'farmyard':
        osm_farmyard.append(item)

    hw = tags.get('highway')
    if hw == 'primary':
        osm_highways_prim.append(item)
    elif hw == 'secondary':
        osm_highways_sec.append(item)
    elif hw == 'track':
        osm_highways_track.append(item)

    if tags.get('bridge') == 'yes':
        osm_bridges.append(item)

print(f"Resumen de capas OSM:")
print(f"  - Cuerpos de agua : {len(osm_water)}")
print(f"  - Parcelas cultivo: {len(osm_farmland)}")
print(f"  - Zonas / Granjas : {len(osm_farmyard)}")
print(f"  - Bosques / Arbol : {len(osm_wood)}")
print(f"  - Rutas primarias : {len(osm_highways_prim)}")
print(f"  - Calles pueblo   : {len(osm_highways_sec)}")
print(f"  - Terracerías     : {len(osm_highways_track)}")
print(f"  - Puentes         : {len(osm_bridges)}")

# ==========================================
# 5. FUNCIÓN DE RENDERIZADO PDA 4K (2K JUGABLE)
# ==========================================
def renderizar_pda(size_total=4096):
    print(f"\nRenderizando PDA {size_total} x {size_total} px (área jugable centrada)...")
    img = Image.new('RGB', (size_total, size_total), COLOR_BORDE_BG)

    # 0. Textura de fondo exterior si existe (montañas y horizonte fuera del área jugable)
    if os.path.exists(BG_TEXTURE_PATH):
        try:
            bg_im = Image.open(BG_TEXTURE_PATH).convert('RGB').resize((size_total, size_total), Image.BILINEAR)
            img.paste(bg_im, (0, 0))
        except Exception as e:
            print(f"Aviso con textura de fondo: {e}")

    draw = ImageDraw.Draw(img)
    escala = size_total / 4096.0
    borde = int(round(1024.0 * escala))
    jugable = int(round(2048.0 * escala))

    # 1. Área jugable central (2048 px centrada en el canvas)
    x0, y0 = borde, borde
    x1, y1 = borde + jugable, borde + jugable
    draw.rectangle([x0, y0, x1, y1], fill=COLOR_PASTO)

    # 2. Cuerpos de agua (Lago Valle Bonito)
    borde_agua = max(1, int(2.5 * escala))
    for item in osm_water:
        if len(item['pts']) >= 3:
            poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.polygon(poly, fill=COLOR_AGUA, outline=COLOR_AGUA_BORDE, width=borde_agua)

    # 3. Islas del lago
    for item in osm_islands:
        if len(item['pts']) >= 3:
            poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.polygon(poly, fill=COLOR_BOSQUE, outline=COLOR_BOSQUE_BORDE, width=max(1, int(1.5 * escala)))

    # 4. Parcelas agrícolas (Campos de cultivo - landuse=farmland de OSM)
    borde_campo = max(1, int(1.2 * escala))
    for item in osm_farmland:
        if len(item['pts']) >= 3:
            poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.polygon(poly, fill=COLOR_CAMPO, outline=COLOR_CAMPO_BORDE, width=borde_campo)

    # 5. Granjas, Patios urbanos y Zona Industrial (OSM landuse=farmyard)
    borde_granja = max(1, int(1.2 * escala))
    for item in osm_farmyard:
        tags = item['tags']
        name = tags.get('name', '').lower()
        is_ind = tags.get('building') == 'industrial' or 'industrial' in name
        is_pueblo = 'pueblo' in name
        
        fill_c = COLOR_INDUSTRIAL if is_ind else (COLOR_PUEBLO if is_pueblo else COLOR_GRANJA)
        out_c = COLOR_INDUSTRIAL_BORDE if is_ind else (COLOR_PUEBLO_BORDE if is_pueblo else COLOR_GRANJA_BORDE)
        
        if len(item['pts']) >= 3:
            poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.polygon(poly, fill=fill_c, outline=out_c, width=borde_granja)

    # Si hay bloques detallados del pueblo en textures.json, renderizarlos para mostrar las calles entre manzanas
    if 'farmyards' in tex_data:
        for fy in tex_data['farmyards']:
            xs = [p[0] for p in fy]
            ys = [p[1] for p in fy]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            # Solo manzanas urbanas / pads de tamaño menor a 300m
            if w < 300 and h < 300 and len(fy) >= 3:
                poly = [local_m_to_canvas_px(p[0], p[1], size_total) for p in fy]
                draw.polygon(poly, fill=COLOR_PUEBLO, outline=COLOR_PUEBLO_BORDE, width=borde_granja)

    # 6. Bosques, arboledas y rompevientos (natural=wood de OSM)
    borde_bosque = max(1, int(1.5 * escala))
    for item in osm_wood:
        is_conifer = item['tags'].get('leaf_type') == 'needleleaved' or 'colina' in item['tags'].get('name', '').lower()
        fill_b = COLOR_BOSQUE_CONIFERA if is_conifer else COLOR_BOSQUE
        out_b = COLOR_BOSQUE_CONIFERA_BORDE if is_conifer else COLOR_BOSQUE_BORDE
        if len(item['pts']) >= 3:
            poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.polygon(poly, fill=fill_b, outline=out_b, width=borde_bosque)

    # 7. Red Vial: Terracerías agrícolas (highway=track)
    w_tierra_casing = max(2, int(4.0 * escala))
    w_tierra_fill = max(1, int(2.4 * escala))
    for item in osm_highways_track:
        if len(item['pts']) >= 2:
            line = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.line(line, fill=COLOR_TIERRA_BORDE, width=w_tierra_casing, joint="curve")
            draw.line(line, fill=COLOR_TIERRA, width=w_tierra_fill, joint="curve")

    # 8. Red Vial: Calles secundarias del pueblo (highway=secondary)
    w_sec_casing = max(3, int(6.5 * escala))
    w_sec_fill = max(2, int(4.5 * escala))
    for item in osm_highways_sec:
        if len(item['pts']) >= 2:
            line = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.line(line, fill=COLOR_ASFALTO_SECUNDARIO_BORDE, width=w_sec_casing, joint="curve")
            draw.line(line, fill=COLOR_ASFALTO_SECUNDARIO, width=w_sec_fill, joint="curve")

    # 9. Red Vial: Rutas principales asfaltadas (highway=primary)
    w_prim_casing = max(4, int(10.0 * escala))
    w_prim_fill = max(3, int(7.5 * escala))
    for item in osm_highways_prim:
        if len(item['pts']) >= 2:
            line = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.line(line, fill=COLOR_ASFALTO_PRIMARIO_BORDE, width=w_prim_casing, joint="curve")
            draw.line(line, fill=COLOR_ASFALTO_PRIMARIO, width=w_prim_fill, joint="curve")

    # 10. Puentes (bridge=yes en OSM)
    w_puente = max(4, int(10.0 * escala))
    for item in osm_bridges:
        if len(item['pts']) >= 2:
            line = [global_to_canvas_px(lat, lon, size_total) for lat, lon in item['pts']]
            draw.line(line, fill=COLOR_PUENTE_BORDE, width=w_puente + max(1, int(2 * escala)), joint="butt")
            draw.line(line, fill=COLOR_PUENTE, width=w_puente, joint="butt")

    # 11. Línea límite del área jugable (marco blanco sutil)
    draw.rectangle([x0, y0, x1, y1], outline=COLOR_LIMITE_JUGABLE, width=max(1, int(2 * escala)))

    return img

# ==========================================
# 6. GENERAR Y GUARDAR IMÁGENES
# ==========================================
os.makedirs(MAP_DIR, exist_ok=True)
os.makedirs(UI_DIR, exist_ok=True)
os.makedirs(os.path.join(MAPA_DIR, 'satellite'), exist_ok=True)
os.makedirs(os.path.join(MAPA_DIR, 'previews'), exist_ok=True)

# 1. Overview 4K (4096 x 4096 px)
print("\nGenerando imagen principal 4K (4096 x 4096 px)...")
img_4k = renderizar_pda(4096)

ruta_satellite_png = os.path.join(MAPA_DIR, 'satellite', 'overview.png')
img_4k.save(ruta_satellite_png, format="PNG")
print(f"  -> Guardado: {ruta_satellite_png}")

if os.path.exists(OVERVIEW_PNG):
    try:
        os.remove(OVERVIEW_PNG)
    except Exception:
        pass
shutil.copy2(ruta_satellite_png, OVERVIEW_PNG)
print(f"  -> Guardado: {OVERVIEW_PNG}")

# 2. Preview 2K (2048 x 2048 px)
print("\nGenerando preview 2K (2048 x 2048 px)...")
img_2k = renderizar_pda(2048)
ruta_preview_png = os.path.join(MAPA_DIR, 'previews', 'satellite_overview.png')
img_2k.save(ruta_preview_png, format="PNG")
print(f"  -> Guardado: {ruta_preview_png}")

# Guardar también como preview.png en la raíz del mod
img_2k.save(PREVIEW_PNG, format="PNG")
print(f"  -> Guardado: {PREVIEW_PNG}")

# 3. Vista 3.8K (3851 x 3851 px)
print("\nGenerando imagen 3.8K (3851 x 3851 px)...")
img_3k = renderizar_pda(3851)
ruta_satellite_preview_png = os.path.join(MAPA_DIR, 'satellite', 'satellite_overview.png')
img_3k.save(ruta_satellite_preview_png, format="PNG")
print(f"  -> Guardado: {ruta_satellite_preview_png}")

# ==========================================
# 7. CONVERSIÓN A DDS (TEXCONV)
# ==========================================
if RUTA_TEXCONV and os.path.exists(RUTA_TEXCONV):
    print("\nConvirtiendo texturas a formato DDS para FS25 usando texconv.exe...")
    
    # 1. overview.dds en MAP_DIR
    cmd_overview = [
        RUTA_TEXCONV,
        '-f', 'BC1_UNORM',
        '-m', '13',
        '-y',
        '-o', MAP_DIR,
        ruta_satellite_png
    ]
    res1 = subprocess.run(cmd_overview, capture_output=True, text=True)
    if res1.returncode == 0:
        print(f"  [OK] Textura DDS 4K generada: {OVERVIEW_DDS}")
        # Copiar también a textures/ui/overview.dds
        try:
            shutil.copy2(OVERVIEW_DDS, UI_OVERVIEW_DDS)
            print(f"  [OK] Sincronizado a UI: {UI_OVERVIEW_DDS}")
        except Exception as e:
            print(f"  [!] Aviso al copiar a UI: {e}")
    else:
        print(f"  [ERROR] texconv (overview): {res1.stderr}")

    # 2. preview.dds en MAPA_DIR
    cmd_preview = [
        RUTA_TEXCONV,
        '-f', 'BC1_UNORM',
        '-m', '11',
        '-y',
        '-o', MAPA_DIR,
        PREVIEW_PNG
    ]
    res2 = subprocess.run(cmd_preview, capture_output=True, text=True)
    if res2.returncode == 0:
        print(f"  [OK] Preview DDS 2K generado: {PREVIEW_DDS}")
        try:
            shutil.copy2(PREVIEW_DDS, UI_PREVIEW_DDS)
            print(f"  [OK] Sincronizado a UI: {UI_PREVIEW_DDS}")
        except Exception as e:
            print(f"  [!] Aviso al copiar a UI: {e}")
    else:
        print(f"  [ERROR] texconv (preview): {res2.stderr}")
else:
    print("\n[AVISO] texconv.exe no encontrado; se generaron los archivos PNG.")

print("\n" + "=" * 65)
print("¡PDA de Farming Simulator 25 completado con éxito a partir de OSM!")
print("=" * 65)
