import json
import os
import subprocess
import sys
from dataclasses import dataclass
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ======================
# CONFIGURAÇÃO GERAL
# ======================
BASE_DIR = Path.cwd()
VIDEO_PATH = os.getenv("VIDEO", "")
CURRENT_PLATFORM = os.getenv("CURRENT_PLATFORM", "vertical")


# === RESOLUÇÃO INTELIGENTE DE ARQUIVOS ===
def resolve_cuts_file(platform):
    env_file = os.getenv("FACECUT_JSON")
    if env_file and os.path.exists(env_file):
        return Path(env_file)

    defaults = [
        f"{platform}_cuts.json",
        "outputs/shorts.json" if platform == "vertical" else None,
        "outputs/vertical_cuts.json",
        "facecuts.json",
    ]
    for fname in defaults:
        if fname:
            fpath = BASE_DIR / fname
            if fpath.exists():
                return fpath
    return BASE_DIR / f"{platform}_cuts.json"


FACECUT_JSON = resolve_cuts_file(CURRENT_PLATFORM)
FACES_CACHE = BASE_DIR / "faces_cache.json"

OUTPUT_DIR = BASE_DIR / "cuts" / CURRENT_PLATFORM
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SUBS_DIR = BASE_DIR / "subs" / CURRENT_PLATFORM
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
VIDEO_LAYOUT = os.getenv("VIDEO_LAYOUT", "auto_podcast")

PARALLEL_WORKERS = int(os.getenv("PARALLEL_WORKERS", max(1, cpu_count() // 2)))

# === ANTI-COPYRIGHT ===
MIRROR_MODE = os.getenv("MIRROR_MODE", "0") == "1"
VIGNETTE_MODE = os.getenv("VIGNETTE_MODE", "0") == "1"
FX_MODE = os.getenv("FX_MODE", "balanced")
SPEED_FACTOR = 1.025

TARGET_W, TARGET_H = 1080, 1920


@dataclass
class RenderConfig:
    width: int
    height: int
    crf: int
    preset: str
    audio_bitrate: str
    v_encoder: str = "libx264"  # Default

    @classmethod
    def for_platform(cls, platform: str) -> "RenderConfig":
        config = cls(TARGET_W, TARGET_H, 26, "veryfast", "96k")
        if platform == "youtube":
            config = cls(1920, 1080, 23, "veryfast", "192k")

        # Detecção de Hardware
        config.v_encoder = detect_best_encoder()
        print(f"   Encoder: {config.v_encoder}")
        return config


def detect_best_encoder() -> str:
    """Tenta detectar se há QSV (Intel) ou NVENC (NVIDIA) disponível no FFmpeg."""
    # Se o usuário desativou explicitamente ou estamos em modo CPU forçado
    if (
        os.getenv("USE_HW_ACCEL", "0") == "0"
        or os.environ.get("MEDIAPIPE_DISABLE_GPU") == "1"
    ):
        return "libx264"

    try:
        res = subprocess.run(
            [FFMPEG_PATH, "-encoders"], capture_output=True, text=True, check=False
        )
        output = res.stdout
        # Prioridade para estabilidade no Docker: libx264
        # NVENC é seguro se os drivers estiverem lá. QSV raramente é.
        if "h264_nvenc" in output:
            return "h264_nvenc"
        if "h264_qsv" in output and os.getenv("FORCE_QSV", "0") == "1":
            return "h264_qsv"
    except Exception:
        pass
    return "libx264"


class FFmpegFilterBuilder:
    def __init__(self, platform: str):
        self.platform = platform
        self.config = RenderConfig.for_platform(platform)
        self.video_filters: List[str] = []
        self.audio_filters: List[str] = []

    def add_crop(self, x: int, y: int, w: int, h: int) -> "FFmpegFilterBuilder":
        self.video_filters.append(f"crop={w}:{h}:{x}:{y}")
        return self

    def add_scale(self, w: int, h: int) -> "FFmpegFilterBuilder":
        self.video_filters.append(f"scale={w}:{h}:flags=fast_bilinear")
        return self

    def add_anti_copyright(self, mode: str, mirror: bool) -> "FFmpegFilterBuilder":
        # CORREÇÃO V164: Removemos o SPEED daqui. Ele deve ser aplicado NO FINAL.
        if mode == "balanced":
            self.video_filters.append("crop=iw*0.98:ih*0.98")
            self.video_filters.append(f"scale={self.config.width}:{self.config.height}")
            self.video_filters.append(
                "eq=contrast=1.1:brightness=-0.02:saturation=1.05"
            )
            self.video_filters.append(
                f"rotate=0.3*PI/180:ow={self.config.width}:oh={self.config.height}"
            )

        if mirror:
            self.video_filters.append("hflip")

        return self

    def add_vignette(self) -> "FFmpegFilterBuilder":
        """Adiciona uma vinheta suave para focar no centro e dar aspecto premium."""
        # angle=0.45 é um valor que cria um foco elegante sem escurecer demais o centro.
        self.video_filters.append("vignette=angle=0.45:mode=backward")
        return self

    def build_video(self) -> str:
        return ",".join(self.video_filters) if self.video_filters else ""

    def build_audio(self) -> str:
        return ",".join(self.audio_filters) if self.audio_filters else ""


class SmartLayoutDetector:
    def __init__(self, faces_data: Dict, clip_w: int, clip_h: int):
        self.faces_data = faces_data
        self.clip_w = clip_w
        self.clip_h = clip_h
        self._cache: Dict[str, Tuple[bool, List]] = {}

    def should_split(
        self, start: float, end: float, samples: int = 15
    ) -> Tuple[bool, List]:
        key = f"{start:.2f}_{end:.2f}"
        if key in self._cache:
            return self._cache[key]
        times = np.linspace(start, end, samples)
        split_votes = 0
        solo_votes = 0
        speaker_positions = []

        for t in times:
            key_t = str(int(t * 1000))
            faces = self.faces_data.get("faces_by_time", {}).get(key_t, [])
            if not faces:
                continue
            if len(faces) < 2:
                solo_votes += 1
                speaker_positions.append(faces[0])
                continue
            faces = sorted(faces, key=lambda x: x["x"])
            speaking = [f for f in faces if f.get("is_speaking", False)]
            if len(speaking) == 1:
                solo_votes += 2
                speaker_positions.append(speaking[0])
                continue
                if (
                    (faces[-1]["x"] + faces[-1]["w"] / 2)
                    - (faces[0]["x"] + faces[0]["w"] / 2)
                ) > self.clip_w * 0.20:  # Aumentado threshold de distância para Split
                    split_votes += 1
                    speaker_positions.extend([faces[0], faces[-1]])
                else:
                    solo_votes += 1
                    speaker_positions.append(
                        faces[0]
                    )  # Adiciona a face mais à esquerda (ou principal)

        # Otimização: se não houve votos claros, tenta pegar as faces mais estáveis
        if not speaker_positions:
            for t in times:
                key_t = str(int(t * 1000))
                faces = self.faces_data.get("faces_by_time", {}).get(key_t, [])
                if faces:
                    speaker_positions.append(max(faces, key=lambda x: x["w"]))

        res = (
            split_votes > solo_votes * 1.5,
            speaker_positions,
        )  # Exige mais votos para Split
        self._cache[key] = res
        return res

    def get_optimal_positions(self, positions: List[Dict]) -> Tuple[int, int]:
        if not positions:
            return self.clip_w // 4, (3 * self.clip_w) // 4
        centers = sorted([f["x"] + f["w"] / 2 for f in positions])
        if len(set(centers)) <= 1:
            return self.clip_w // 4, (3 * self.clip_w) // 4
        return int(np.mean(centers[: len(centers) // 2])), int(
            np.mean(centers[len(centers) // 2 :])
        )


def render_optimized(
    video_path, start, end, v_filter, a_filter, out_path, config, ass_path=None
):
    # A duração deve ser calculada baseada no output final (acelerado)
    duration_input = end - start
    duration_output = (
        duration_input / SPEED_FACTOR if SPEED_FACTOR != 1.0 else duration_input
    )

    # 1. Aplica Filtros Visuais (Crop, Scale, Anti-Copyright Visual)
    chain = v_filter

    # 2. Aplica Legenda (ANTES DO SPEED)
    # Isso garante que a legenda "colada" no frame sofra a aceleração junto com o vídeo
    if ass_path and ass_path.exists():
        esc_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        chain = f"{chain},ass='{esc_ass}'" if chain else f"ass='{esc_ass}'"

    # 3. Aplica Speed (POR ÚLTIMO)
    if SPEED_FACTOR != 1.0:
        chain = (
            f"{chain},setpts=PTS/{SPEED_FACTOR}"
            if chain
            else f"setpts=PTS/{SPEED_FACTOR}"
        )
        # Aplica speed no áudio também
        a_filter = (
            f"{a_filter},atempo={SPEED_FACTOR}"
            if a_filter
            else f"atempo={SPEED_FACTOR}"
        )

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-v",
        "error",
    ]

    cmd.extend(
        [
            "-ss",
            str(start),
            "-i",
            video_path,
            "-t",
            str(duration_output),
            "-map_metadata",
            "-1",
            "-sn",
            "-dn",
        ]
    )

    if chain:
        cmd.extend(["-vf", chain])
    if a_filter:
        cmd.extend(["-af", a_filter])

    # Configurações do Encoder
    if config.v_encoder == "h264_qsv":
        cmd.extend(
            [
                "-c:v",
                "h264_qsv",
                "-global_quality",
                str(config.crf),
                "-look_ahead",
                "0",
                "-pix_fmt",
                "nv12",
            ]
        )
    elif config.v_encoder == "h264_nvenc":
        cmd.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p1",  # p1 é fastest no nvenc
                "-cq",
                str(config.crf),
            ]
        )
    else:
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                config.preset,
                "-crf",
                str(config.crf),
                "-threads",
                "1",
            ]
        )

    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            config.audio_bitrate,
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Erro FFmpeg: {e.stderr.decode(errors='ignore')}")
        return False


def render_split_screen_optimized(
    video_path,
    start,
    end,
    xl,
    xr,
    cw,
    clip_h,
    out_path,
    config,
    fx_mode,
    mirror,
    ass_path=None,
):
    duration_output = (
        (end - start) / SPEED_FACTOR if SPEED_FACTOR != 1.0 else (end - start)
    )

    fc = (
        f"split=2[top][bot];[top]crop={cw}:{clip_h}:{xl}:0,scale={config.width}:{config.height // 2}:flags=fast_bilinear[t];"
        f"[bot]crop={cw}:{clip_h}:{xr}:0,scale={config.width}:{config.height // 2}:flags=fast_bilinear[b];[t][b]vstack[v_stack]"
    )

    cur_v = "v_stack"

    # 1. Efeitos Visuais
    if fx_mode == "balanced":
        fc += (
            f";[{cur_v}]eq=contrast=1.1:brightness=-0.02:saturation=1.05,"
            f"crop=iw*0.98:ih*0.98,scale={config.width}:{config.height},"
            f"rotate=0.3*PI/180:ow={config.width}:oh={config.height}[v_fx]"
        )
        cur_v = "v_fx"

    if mirror:
        fc += f";[{cur_v}]hflip[v_mirror]"
        cur_v = "v_mirror"

    # NOVO: Filtro de Vinheta no Split (Opcional)
    if os.getenv("VIGNETTE_MODE", "0") == "1":
        fc += f";[{cur_v}]vignette=angle=0.45:mode=backward[v_vig]"
        cur_v = "v_vig"

    # 2. Legenda (ANTES DO SPEED)
    if ass_path and ass_path.exists():
        esc_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        fc += f";[{cur_v}]ass='{esc_ass}'[v_sub]"
        cur_v = "v_sub"

    # 3. Speed Change (VÍDEO)
    if SPEED_FACTOR != 1.0:
        fc += f";[{cur_v}]setpts=PTS/{SPEED_FACTOR}[v_final]"
        cur_v = "v_final"

    # 4. Speed Change (ÁUDIO)
    amap = "0:a"
    if SPEED_FACTOR != 1.0:
        fc += f";[0:a]atempo={SPEED_FACTOR}[a_final]"
        amap = "[a_final]"

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-v",
        "error",
    ]

    cmd.extend(
        [
            "-ss",
            str(start),
            "-i",
            video_path,
            "-t",
            str(duration_output),
            "-filter_complex",
            fc,
            "-map",
            f"[{cur_v}]",
            "-map",
            amap,
            "-map_metadata",
            "-1",
        ]
    )

    if config.v_encoder == "h264_qsv":
        cmd.extend(
            ["-c:v", "h264_qsv", "-global_quality", str(config.crf), "-pix_fmt", "nv12"]
        )
    elif config.v_encoder == "h264_nvenc":
        cmd.extend(["-c:v", "h264_nvenc", "-preset", "p1", "-cq", str(config.crf)])
    else:
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                config.preset,
                "-crf",
                str(config.crf),
                "-threads",
                "1",
            ]
        )

    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            config.audio_bitrate,
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"Erro split: {e}")
        return False


def process_single_cut(args):
    (
        i,
        cut,
        faces,
        clip_w,
        clip_h,
        video_path,
        output_dir,
        subs_dir,
        current_platform,
    ) = args
    try:
        s, e = float(cut["start"]), float(cut["end"])
        slug = cut.get("slug", f"cut_{i:02}")
        out = output_dir / f"{slug}.mp4"
        ass = subs_dir / f"{slug}.ass"

        # Removido SKIP automático para garantir que alterações de filtros (como vinheta)
        # sejam aplicadas quando o usuário clica em renderizar novamente.
        # if out.exists():
        #     return f"SKIP {slug}: ja existe"

        cfg = RenderConfig.for_platform(current_platform)

        if current_platform == "youtube":
            builder = (
                FFmpegFilterBuilder(current_platform)
                .add_scale(1920, 1080)
                .add_anti_copyright(FX_MODE, MIRROR_MODE)
            )
            if VIGNETTE_MODE:
                builder.add_vignette()

            if render_optimized(
                video_path,
                s,
                e,
                builder.build_video(),
                builder.build_audio(),
                out,
                cfg,
                ass,
            ):
                return f"OK {slug}: YOUTUBE"
        else:
            ld = SmartLayoutDetector(faces, clip_w, clip_h)
            split, pos = ld.should_split(s, e)

            if split:
                cx_l, cx_r = ld.get_optimal_positions(pos)
                cw = int(clip_h * (TARGET_W / TARGET_H))
                cw = cw if cw <= clip_w // 2 else clip_w // 2
                xl = max(0, min(int(cx_l - cw / 2), clip_w - cw))
                xr = max(0, min(int(cx_r - cw / 2), clip_w - cw))
                if render_split_screen_optimized(
                    video_path,
                    s,
                    e,
                    xl,
                    xr,
                    cw,
                    clip_h,
                    out,
                    cfg,
                    FX_MODE,
                    MIRROR_MODE,
                    ass,
                ):
                    return f"OK {slug}: SPLIT"
            else:
                if pos:
                    # HEURÍSTICA DE FOCO V2:
                    # 1. Filtra apenas frames onde alguém de fato estava falando
                    speaking_only = [f for f in pos if f.get("is_speaking", False)]

                    if speaking_only:
                        # Se temos falantes claros, focamos na mediana da posição DELES
                        centers = [f["x"] + f["w"] / 2 for f in speaking_only]
                        xc = float(np.median(centers))
                    else:
                        # Se ninguém falou claramente (ex: reação), focamos na face mais central ou maior
                        # Aqui usamos a mediana de todas as posições detectadas para estabilidade
                        centers = [f["x"] + f["w"] / 2 for f in pos]
                        xc = float(np.median(centers))
                else:
                    xc = clip_w / 2
                cw = int(clip_h * 9 / 16)
                xp = max(0, min(int(xc - cw / 2), clip_w - cw))
                builder = (
                    FFmpegFilterBuilder(current_platform)
                    .add_crop(xp, 0, cw, clip_h)
                    .add_scale(TARGET_W, TARGET_H)
                    .add_anti_copyright(FX_MODE, MIRROR_MODE)
                )
                if VIGNETTE_MODE:
                    builder.add_vignette()

                if render_optimized(
                    video_path,
                    s,
                    e,
                    builder.build_video(),
                    builder.build_audio(),
                    out,
                    cfg,
                    ass,
                ):
                    return f"OK {slug}: SOLO"

        return f"ERRO {slug}: Falha"
    except Exception as e:
        return f"ERRO {i}: {e}"


def load_faces():
    if not FACES_CACHE.exists():
        return None
    try:
        return json.load(open(FACES_CACHE, "r", encoding="utf-8"))
    except Exception:
        return None


def main():
    print("=" * 70)
    print("RENDER ANTI-COPYRIGHT V164 (Correct Sync)")
    print(f"   Speed: {SPEED_FACTOR}x (Applied AFTER Subtitles)")
    print("=" * 70)

    if not FACECUT_JSON.exists():
        print("ERRO CRITICO: Arquivo de cortes nao encontrado.")
        return

    print(f"Cortes: {FACECUT_JSON.name}")
    print(
        f"Faces: {'OK' if FACES_CACHE.exists() else 'Aviso: Nao encontrado (Usando centro)'}"
    )

    with open(FACECUT_JSON, "r", encoding="utf-8") as f:
        cuts = json.load(f)
    faces = load_faces() or {}

    if CURRENT_PLATFORM != "youtube":
        cap = cv2.VideoCapture(VIDEO_PATH)
        clip_w = int(cap.get(3))
        clip_h = int(cap.get(4))
        cap.release()
    else:
        clip_w, clip_h = 1920, 1080

    args = [
        (
            i,
            c,
            faces,
            clip_w,
            clip_h,
            VIDEO_PATH,
            OUTPUT_DIR,
            SUBS_DIR,
            CURRENT_PLATFORM,
        )
        for i, c in enumerate(cuts)
    ]

    with Pool(processes=PARALLEL_WORKERS) as pool:
        # Usamos imap para poder envolver com tqdm e tqdm.write
        pbar = tqdm(
            total=len(args), desc=f"Renderizando {CURRENT_PLATFORM}", unit="clip"
        )
        results = []

        for res in pool.imap(process_single_cut, args):
            results.append(res)
            if "ERRO" in res:
                pbar.write(f"[ERRO] {res}")
            else:
                pbar.write(f"[OK] {res}")
            pbar.update(1)
        pbar.close()

    print("\nRenderizacao completa!")


if __name__ == "__main__":
    main()
