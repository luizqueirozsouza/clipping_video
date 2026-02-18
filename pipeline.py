import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

# =========================
# ENV & CONFIG
# =========================

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(override=False)

VIDEO = os.getenv("VIDEO", "input.mp4")
PLATFORMS = os.getenv("PLATFORMS", "vertical,youtube")

SUBS_DIR = Path(os.getenv("SUBS_DIR", "subs"))
OUT_DIR = Path(os.getenv("OUTPUT_DIR", "cuts"))

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

SUBS_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

platforms_list = [p.strip() for p in PLATFORMS.split(",") if p.strip()]

for platform in platforms_list:
    (OUT_DIR / platform).mkdir(exist_ok=True)
    (SUBS_DIR / platform).mkdir(exist_ok=True)


# =========================
# PLATFORM → JSON MAP
# =========================

PLATFORM_JSON_MAP = {
    "vertical": "outputs/vertical_cuts.json",  # subtópicos
    "youtube": "outputs/youtube_long.json",  # tópicos
}


# =========================
# SCRIPT RUNNER
# =========================


def run_script(script_name):
    print(f"\n{'=' * 50}")
    print(f"▶️ Iniciando: {script_name}")
    print(f"{'=' * 50}")

    cmd = [sys.executable, script_name]
    env_vars = os.environ.copy()
    env_vars["PYTHONIOENCODING"] = "utf-8"

    try:
        subprocess.run(cmd, check=True, env=env_vars)
        print(f"✅ {script_name} concluído.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao executar {script_name}: {e}")
        sys.exit(1)


# =========================
# PIPELINE
# =========================

print("🎬 PIPELINE INICIADO (TOPIC-FIRST)")
print(f"📹 Vídeo: {VIDEO}")
print(f"📱 Plataformas: {', '.join(platforms_list)}")

# 1. Transcrição + detecção de rosto/falante
run_script("facecut.py")

# 2. Inteligência Semântica (tópicos + subtópicos)
run_script("segment_agent.py")

# 3. Renderização por plataforma
print("\n🎬 Iniciando Renderização...")

for platform in platforms_list:
    json_file = PLATFORM_JSON_MAP.get(platform)

    if not json_file or not os.path.exists(json_file):
        print(f"⚠️ JSON não encontrado para {platform}, pulando...")
        continue

    print(f"\n👉 Plataforma: {platform.upper()}")

    os.environ["CURRENT_PLATFORM"] = platform
    os.environ["FACECUT_JSON"] = json_file
    os.environ["SUBS_DIR"] = str(SUBS_DIR / platform)
    os.environ["OUTPUT_DIR"] = str(OUT_DIR / platform)

    run_script("generate_karaoke_from_segment.py")
    run_script("render_facecuts.py")


# =========================
# SUMMARY
# =========================

print(f"\n{'=' * 60}")
print("🎉 PIPELINE FINALIZADO")
print(f"{'=' * 60}")

if (OUT_DIR / "vertical").exists():
    print(f"   • Shorts: {len(list((OUT_DIR / 'vertical').glob('*.mp4')))} vídeos")

if (OUT_DIR / "youtube").exists():
    print(
        f"   • YouTube Longo: {len(list((OUT_DIR / 'youtube').glob('*.mp4')))} vídeos"
    )
