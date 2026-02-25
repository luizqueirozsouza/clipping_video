import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ======================
# CONFIG
# ======================
BASE_DIR = Path.cwd()
VIDEO = os.getenv("VIDEO", "input.mp4")

CURRENT_PLATFORM = os.getenv("CURRENT_PLATFORM", "vertical")

# Arquivo de cortes (agora sempre vem do pipeline/app)
CUTS_JSON_ENV = os.getenv("FACECUT_JSON")
CUTS_JSON = BASE_DIR / CUTS_JSON_ENV if CUTS_JSON_ENV else None

OUT_DIR = BASE_DIR / "subs" / CURRENT_PLATFORM
OUT_DIR.mkdir(exist_ok=True, parents=True)

WORDS_CACHE = BASE_DIR / "transcript_words.json"
SEGMENTS_CACHE = BASE_DIR / "transcript_segments.json"

SUB_FONT = os.getenv("SUB_FONT", "Arial Black")
SUB_COLOR = os.getenv("SUB_COLOR", "Amarelo")
SUB_UPPERCASE = os.getenv("SUB_UPPERCASE", "1") == "1"

COLOR_MAP = {
    "Amarelo": "&H0000FFFF",
    "Verde": "&H0000FF00",
    "Azul": "&H00FFFF00",
    "Rosa": "&H00FF00FF",
}
ACTIVE_COLOR = COLOR_MAP.get(SUB_COLOR, "&H0000FFFF")

STYLE_BASE = {
    "font": SUB_FONT,
    "fontsize": 65,
    "bold": 1,
    "outline": 4,
    "alignment": 2,
    "margin_v": 450,
}


# ======================
# UTILS
# ======================
def time_to_ass(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def group_words_into_lines(words):
    lines, current, clen = [], [], 0
    for w in words:
        wl = len(w["word"])
        if clen + wl > 20:
            if current:
                lines.append(current)
            current, clen = [], 0
        current.append(w)
        clen += wl + 1
    if current:
        lines.append(current)
    return lines


def estimate_words_from_segments():
    if not SEGMENTS_CACHE.exists():
        return []

    segments = json.load(open(SEGMENTS_CACHE, encoding="utf-8"))
    words = []

    for seg in segments:
        text = seg.get("text", "")
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        parts = text.split()

        if not parts:
            continue

        dur = (end - start) / len(parts)
        t = start
        for p in parts:
            words.append({"word": p, "start": t, "end": t + dur})
            t += dur

    return words


# ======================
# MAIN
# ======================
def main():
    # ----------------------
    # Determina plataformas
    # ----------------------
    platforms = []

    if CUTS_JSON and CUTS_JSON.exists():
        platforms = [CURRENT_PLATFORM]
    else:
        # Fallback compatível com o novo padrão
        for p, name in {
            "vertical": "outputs/vertical_cuts.json",
            "youtube": "outputs/youtube_long.json",
        }.items():
            if (BASE_DIR / name).exists():
                platforms.append(p)

    if not platforms:
        print("Erro: Nenhum arquivo de cortes encontrado.")
        return

    # ----------------------
    # Loop por plataforma
    # ----------------------
    for platform in platforms:
        print(f"\nGerando legendas: {platform.upper()}")

        cuts_path = (
            CUTS_JSON
            if CUTS_JSON and CURRENT_PLATFORM == platform
            else BASE_DIR
            / (
                "outputs/vertical_cuts.json"
                if platform == "vertical"
                else "outputs/youtube_long.json"
            )
        )

        if not cuts_path.exists():
            print(f"Aviso: Cortes nao encontrados: {cuts_path}")
            continue

        cuts = json.load(open(cuts_path, encoding="utf-8"))

        # ----------------------
        # Palavras
        # ----------------------
        words = []
        if WORDS_CACHE.exists():
            try:
                data = json.load(open(WORDS_CACHE, encoding="utf-8"))
                if isinstance(data, list) and data and "words" in data[0]:
                    words = data[0]["words"]
                elif isinstance(data, list):
                    words = data
            except:
                pass

        if not words:
            print("Aviso: Usando estimativa matematica.")
            words = estimate_words_from_segments()

        if not words:
            print("Erro: Nenhuma palavra disponivel.")
            continue

        # ----------------------
        # Estilo por plataforma
        # ----------------------
        is_youtube = platform == "youtube"
        res_x, res_y = (1920, 1080) if is_youtube else (1080, 1920)

        style = STYLE_BASE.copy()
        if is_youtube:
            style.update({"fontsize": 45, "margin_v": 60, "outline": 2})

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style["font"]},{style["fontsize"]},{ACTIVE_COLOR},&H00FFFFFF,&H00000000,&H80000000,{style["bold"]},0,0,0,100,100,0,0,1,{style["outline"]},2,{style["alignment"]},80,80,{style["margin_v"]},1
[Events]
Format: Layer, Start, End, Style, Text
"""

        out_dir = BASE_DIR / "subs" / platform
        out_dir.mkdir(exist_ok=True, parents=True)

        ok = 0
        for idx, cut in enumerate(cuts):
            # === ALTERAÇÃO: Tenta usar o slug, fallback para cut_XX ===
            slug_name = cut.get("slug")
            if not slug_name:
                slug_name = f"cut_{idx:02}"

            ass_file = out_dir / f"{slug_name}.ass"
            # ==========================================================

            sc, ec = float(cut["start"]), float(cut["end"])

            clip_words = []
            for w in words:
                if w["start"] >= sc - 0.5 and w["end"] <= ec + 0.5:
                    clean_word = re.sub(r"\[\d+\]", "", w["word"]).strip()
                    if not clean_word:
                        continue
                    clip_words.append(
                        {
                            "word": clean_word.upper() if SUB_UPPERCASE else clean_word,
                            "start": max(0, w["start"] - sc),
                            "end": w["end"] - sc,
                        }
                    )

            if not clip_words:
                continue

            lines = group_words_into_lines(clip_words)

            with open(ass_file, "w", encoding="utf-8") as f:
                f.write(header)
                for line in lines:
                    l_start = line[0]["start"]
                    l_end = line[-1]["end"] + 0.1
                    text = "".join(
                        f"{{\\k{max(1, int((w['end'] - w['start']) * 100))}}}{w['word']} "
                        for w in line
                    )
                    f.write(
                        f"Dialogue: 0,{time_to_ass(l_start)},{time_to_ass(l_end)},Default,{text.strip()}\n"
                    )

            if ass_file.exists():
                ok += 1

        print(f"OK [{platform}] Legendas criadas: {ok}/{len(cuts)}")


if __name__ == "__main__":
    main()
