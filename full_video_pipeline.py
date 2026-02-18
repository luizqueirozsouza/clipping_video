import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv(override=True)

# ======================
# CONFIGURAÇÕES
# ======================
BASE_DIR = Path.cwd()
WORKSPACE_DIR = BASE_DIR / "projeto_atual"

# Resolução do Vídeo
ENV_VIDEO = os.getenv("VIDEO", "")
if ENV_VIDEO and os.path.exists(ENV_VIDEO):
    VIDEO_PATH = Path(ENV_VIDEO)
else:
    files = list(WORKSPACE_DIR.glob("video_master_*.mp4"))
    if files:
        files.sort(key=os.path.getmtime, reverse=True)
        VIDEO_PATH = files[0]
        print(f"⚠️ Aviso: VIDEO env vazio. Usando automático: {VIDEO_PATH.name}")
    else:
        VIDEO_PATH = BASE_DIR / "input.mp4"

FULL_CUTS_JSON = BASE_DIR / "full_video_cuts.json"
OUTPUT_DIR = BASE_DIR / "cuts" / "full_video"
SUBS_DIR = BASE_DIR / "subs" / "full_video"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SUBS_DIR.mkdir(parents=True, exist_ok=True)

# Resolução de Binários
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")


def resolve_ffprobe():
    """Tenta encontrar o ffprobe vizinho ao ffmpeg"""
    if "ffmpeg" in FFMPEG_PATH:
        candidate = FFMPEG_PATH.replace("ffmpeg", "ffprobe")
        if os.path.exists(candidate):
            return candidate
        # Tenta sem extensão em linux/mac
        candidate_no_ext = os.path.splitext(candidate)[0]
        if os.path.exists(candidate_no_ext):
            return candidate_no_ext

    sys_probe = shutil.which("ffprobe")
    return sys_probe if sys_probe else "ffprobe"


FFPROBE_PATH = resolve_ffprobe()

# ======================
# FUNÇÕES DE DURAÇÃO
# ======================


def get_duration_via_ffmpeg(video_path):
    """Lê a duração usando o output do ffmpeg -i (Fallback robusto)"""
    print(f"ℹ️ Tentando fallback via FFmpeg: {FFMPEG_PATH}")
    cmd = [FFMPEG_PATH, "-i", str(video_path)]

    try:
        # FFmpeg manda info para o stderr
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # Procura padrão: Duration: 00:03:52.83
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
        if match:
            h, m, s = map(float, match.groups())
            seconds = h * 3600 + m * 60 + s
            print(f"✅ Duração (FFmpeg fallback): {seconds:.2f}s")
            return seconds
    except Exception as e:
        print(f"⚠️ Falha no FFmpeg fallback: {e}")
    return 0


def get_video_duration(video_path):
    if not os.path.exists(video_path):
        return 0

    # 1. FFprobe (Ideal)
    try:
        cmd = [
            FFPROBE_PATH,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        # Configuração para Windows (esconder janela)
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, startupinfo=startupinfo
        )
        dur = float(result.stdout.strip())
        print(f"✅ Duração (FFprobe): {dur:.2f}s")
        return dur
    except Exception:
        pass  # Silenciosamente tenta o próximo

    # 2. FFmpeg (Geralmente funciona se o binário ffmpeg existe)
    dur = get_duration_via_ffmpeg(video_path)
    if dur > 0:
        return dur

    # 3. OpenCV (Última chance)
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            dur = frames / fps if fps > 0 else 0
            cap.release()
            print(f"✅ Duração (OpenCV): {dur:.2f}s")
            return dur
    except:
        pass

    return 0


def create_full_cut_json(duration):
    """Cria JSON de corte único"""
    cut = {
        "start": 0.0,
        "end": duration,
        "duration": duration,
        "title": "Vídeo Completo (Anti-Copyright)",
        "slug": "video_completo_anticopyright",
        "score": 100,
    }

    with open(FULL_CUTS_JSON, "w", encoding="utf-8") as f:
        json.dump([cut], f, indent=2, ensure_ascii=False)

    print(f"✅ JSON Criado: {FULL_CUTS_JSON.name}")


def run_script(script_name, env_vars):
    print(f"\n{'=' * 50}\n▶️ Executando: {script_name}\n{'=' * 50}")
    cmd = [sys.executable, script_name]
    try:
        subprocess.run(cmd, check=True, env=env_vars)
        print(f"✅ {script_name} concluído.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro ao executar {script_name}: {e}")
        sys.exit(1)


def main():
    print("🎬 PIPELINE VÍDEO COMPLETO (V163)")

    if not VIDEO_PATH or not VIDEO_PATH.exists():
        print(f"❌ Arquivo não encontrado: {VIDEO_PATH}")
        return

    print(f"   Arquivo: {VIDEO_PATH.name}")

    if not os.path.exists("transcript_segments.json"):
        print("❌ Transcrição ausente. Execute a análise no App primeiro.")
        return

    # Obtém duração
    duration = get_video_duration(VIDEO_PATH)
    if duration == 0:
        print(
            "❌ FALHA FATAL: Não foi possível ler a duração do vídeo por nenhum método."
        )
        return

    # Gera JSON
    create_full_cut_json(duration)

    # Configura ambiente
    processing_env = os.environ.copy()
    processing_env["VIDEO"] = str(VIDEO_PATH)
    processing_env["CURRENT_PLATFORM"] = (
        "youtube"  # Força modo horizontal/alta qualidade
    )
    processing_env["FACECUT_JSON"] = str(FULL_CUTS_JSON)
    processing_env["OUTPUT_DIR"] = str(OUTPUT_DIR)
    processing_env["SUBS_DIR"] = str(SUBS_DIR)

    # Anti-Copyright Settings
    processing_env["FX_MODE"] = "balanced"
    processing_env["MIRROR_MODE"] = "0"  # Não espelhar vídeo longo (legibilidade)
    processing_env["VIDEO_LAYOUT"] = "single"

    # Executa Pipeline
    run_script("generate_karaoke_from_segment.py", processing_env)
    run_script("render_facecuts.py", processing_env)

    final_file = OUTPUT_DIR / "video_completo_anticopyright.mp4"
    if final_file.exists():
        print(f"\n🎉 SUCESSO! Vídeo pronto em:\n   {final_file}")
    else:
        print("\n❌ Algo deu errado na renderização.")


if __name__ == "__main__":
    main()
