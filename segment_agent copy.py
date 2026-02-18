import json
import os
import re
import unicodedata  # <--- NOVO
from dataclasses import asdict, dataclass
from typing import Dict, List

from dotenv import load_dotenv

import llm_service

load_dotenv()

# =========================
# CONFIGURAÇÕES
# =========================

TRANSCRIPT_PATH = "transcript_segments.json"
OUTPUT_DIR = "."
TOPICS_OUT = "structure_topics.json"
SHORTS_OUT = "vertical_cuts.json"
LONGS_OUT = "youtube_cuts.json"
METADATA_OUT = "video_descriptions.json"

ORIGINAL_URL = os.getenv("ORIGINAL_URL", "[INSERIR LINK DO VÍDEO COMPLETO]")

MIN_SHORT_SEC = 40
MAX_SHORT_SEC = 180
MIN_LONG_SEC = 180
MAX_LONG_SEC = 600

# =========================
# DATA MODELS
# =========================


@dataclass
class Subtopic:
    title: str
    start_index: int
    end_index: int
    start_time: float
    end_time: float
    summary: str
    duration: float


@dataclass
class Topic:
    title: str
    start_time: float
    end_time: float
    summary: str
    subtopics: List[Subtopic]
    duration: float


# =========================
# CONTEXTO & TEMPLATE
# =========================

CHANNEL_CONTEXT = """
NOME DO CANAL: Tempo Restante
IDENTIDADE: Canal reformado, focado em evangelismo bíblico (Lei, Pecado, Graça, Juízo).
ESTILO: Ray Comfort, Paul Washer, Voddie Baucham.

VOCÊ DEVE SEGUIR ESTRITAMENTE ESTE TEMPLATE DE DESCRIÇÃO:

[INSIRA AQUI UM VERSÍCULO CHAVE RELACIONADO AO TRECHO]
Ex: “Pelas obras da lei nenhuma carne será justificada” (Romanos 3:20).

Neste vídeo, [EXPLIQUE O CERNE DA MENSAGEM DO VÍDEO]:
👉 [Ponto chave 1]
👉 [Ponto chave 2]
👉 [Ponto chave 3]

📖 Aprendemos que:
[Explicação teológica mais profunda, usando termos como Lei, Graça, Justificação, Santidade]
[Se possível, cite outro versículo de apoio]

✝️ [Conclusão focada em Cristo e na Cruz - A solução para o pecado]

[Se o vídeo falar de evangelismo moderno/falso evangelho, insira um parágrafo de confronto aqui]

🙏 Se essa mensagem foi clara e edificante para você:
Curta o vídeo 👍
Comente o que mais te confrontou ou ensinou 💬
Compartilhe com alguém que precisa ouvir a verdade 🚀
Inscreva-se no canal para acompanhar essa série 📌

“[Frase de impacto curta resumindo o vídeo]” ✝️

Que Deus use essa palavra para conduzir muitos da convicção de pecado à fé salvadora em Cristo.
"""

# =========================
# HELPERS
# =========================


def safe_json_parse(text: str) -> Dict:
    try:
        return json.loads(text)
    except:
        pass
    try:
        text = text.replace("```json", "").replace("```", "")
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
    except:
        pass
    return {}


def slugify(text):
    """Transforma 'O Teste Moral!' em 'o-teste-moral' para nome de arquivo seguro."""
    if not text:
        return "corte-sem-nome"
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "-", text)


# =========================
# PROMPTS
# =========================


def build_topic_prompt(transcript_excerpt: str, max_id: int) -> str:
    return f"""
Atue como editor sênior. Estruture o conteúdo em TÓPICOS.
REGRAS: Priorize tópicos completos (início/meio/fim). Use subtópicos apenas se necessário.
FORMATO JSON PURO:
{{ "topics": [ {{ "title": "...", "summary": "...", "start_index": 0, "end_index": 15, "subtopics": [] }} ] }}
TRANSCRIÇÃO:
{transcript_excerpt}
"""


def build_metadata_prompt(video_text: str, duration: float, format_type: str) -> str:
    return f"""
{CHANNEL_CONTEXT}

TAREFA: Escreva os metadados para este trecho de {duration:.0f}s.
TEXTO DO VÍDEO: "{video_text}"

IMPORTANTE: 
1. Adapte o template para o CONTEÚDO ESPECÍFICO deste texto. 
2. Escolha um versículo que faça sentido com o que foi falado.
3. Mantenha a formatação exata (emojis, quebras de linha).

RETORNE APENAS JSON:
{{
    "youtube_title": "Título Clickbait Santo (Max 60 chars)",
    "youtube_description": "Texto completo seguindo o TEMPLATE acima.",
    "tags": ["teologia", "evangelho", "pecado", "graça", "tempo restante"]
}}
"""


# =========================
# LÓGICA CORE
# =========================


def load_transcript() -> List[Dict]:
    if not os.path.exists(TRANSCRIPT_PATH):
        raise FileNotFoundError(f"{TRANSCRIPT_PATH} não encontrado.")
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        for item in data:
            item["start"], item["end"] = float(item["start"]), float(item["end"])
        return data


def get_text_segment(segments: List[Dict], start_time: float, end_time: float) -> str:
    return " ".join(
        [s["text"] for s in segments if s["end"] > start_time and s["start"] < end_time]
    )


def call_llm_for_structure(segments: List[Dict]) -> Dict:
    transcript_text = "\n".join(f"[ID {i}] {s['text']}" for i, s in enumerate(segments))
    prompt = build_topic_prompt(transcript_text, len(segments) - 1)
    print("⏳ Analisando estrutura narrativa via LLM...")
    response = llm_service.call_llm(prompt, json_mode=True, temperature=0.2)
    data = safe_json_parse(response)
    if not data:
        try:
            data = json.loads(llm_service.clean_json_response(response))
        except:
            return {"topics": []}
    return {"topics": data} if isinstance(data, list) else data


def generate_metadata_for_cut(
    cut_item: Dict, segments: List[Dict], format_type: str
) -> Dict:
    full_text = get_text_segment(segments, cut_item["start"], cut_item["end"])[:5000]
    prompt = build_metadata_prompt(full_text, cut_item["duration"], format_type)

    try:
        response = llm_service.call_llm(prompt, json_mode=True, temperature=0.4)
        meta = safe_json_parse(response)
        if not meta:
            raise ValueError("JSON inválido")

        desc = meta.get("youtube_description", "")
        if "Vídeo Original" not in desc:
            desc += f"\n\n🎥 Vídeo Original: {ORIGINAL_URL}"

        meta["youtube_description"] = desc
        return meta
    except:
        return {
            "youtube_title": cut_item["title"],
            "youtube_description": f"A Bíblia é clara...\n\n🎥 Vídeo Original: {ORIGINAL_URL}\n\nVocê está pronto?",
            "tags": ["evangelho"],
        }


def process_structure(segments: List[Dict], llm_data: Dict) -> List[Topic]:
    topics = []
    for t in llm_data.get("topics", []):
        try:
            t_start = segments[t.get("start_index", 0)]["start"]
            t_end = segments[t.get("end_index", 0)]["end"]
            subs = []
            for st in t.get("subtopics", []):
                st_start = segments[st.get("start_index", 0)]["start"]
                st_end = segments[st.get("end_index", 0)]["end"]
                subs.append(
                    Subtopic(
                        st["title"],
                        0,
                        0,
                        st_start,
                        st_end,
                        st.get("summary", ""),
                        st_end - st_start,
                    )
                )
            topics.append(
                Topic(
                    t["title"],
                    t_start,
                    t_end,
                    t.get("summary", ""),
                    subs,
                    t_end - t_start,
                )
            )
        except:
            continue
    return topics


def create_cut_item(title, start, end, duration, summary):
    # GERA O SLUG AQUI (Baseado no título)
    safe_slug = slugify(title)
    return {
        "start": start,
        "end": end,
        "duration": duration,
        "title": title,
        "youtube_title": title,
        "summary": summary,
        "score": 100,
        "slug": safe_slug,  # <--- Agora o slug é o nome do arquivo (ex: a-lei-revela-o-pecado)
    }


def export_results(topics: List[Topic], segments: List[Dict]):
    try:
        with open(TOPICS_OUT, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in topics], f, ensure_ascii=False, indent=2)
    except:
        pass

    shorts, longs = [], []
    print("\n🔍 SELECIONANDO CORTES:")

    for t in topics:
        if MIN_SHORT_SEC <= t.duration <= MAX_SHORT_SEC:
            print(f"   ✅ [Shorts] Tópico: '{t.title}' ({t.duration:.1f}s)")
            shorts.append(
                create_cut_item(
                    t.title, t.start_time, t.end_time, t.duration, t.summary
                )
            )
        elif t.duration > MAX_SHORT_SEC:
            for s in t.subtopics:
                if MIN_SHORT_SEC <= s.duration <= MAX_SHORT_SEC:
                    print(
                        f"      🔹 [Shorts] Subtópico: '{s.title}' ({s.duration:.1f}s)"
                    )
                    shorts.append(
                        create_cut_item(
                            s.title, s.start_time, s.end_time, s.duration, s.summary
                        )
                    )

        if MIN_LONG_SEC <= t.duration <= MAX_LONG_SEC:
            print(f"   📺 [YouTube] Longo: '{t.title}' ({t.duration:.1f}s)")
            longs.append(
                create_cut_item(
                    t.title, t.start_time, t.end_time, t.duration, t.summary
                )
            )

    print("\n✍️ GERANDO DESCRIÇÕES (ESTILO 'TEMPO RESTANTE')...")
    meta_db = {}

    for q, label, out_file in [
        (shorts, "Short", SHORTS_OUT),
        (longs, "Longo", LONGS_OUT),
    ]:
        for cut in q:
            print(
                f"   {label} ({cut['duration']:.0f}s): {cut['slug']}...",
                end="",
                flush=True,
            )  # Loga o slug
            meta = generate_metadata_for_cut(cut, segments, label)
            cut["youtube_title"] = meta.get("youtube_title", cut["title"])
            meta_db[cut["slug"]] = {
                "type": label.lower(),
                "filename": f"{cut['slug']}.mp4",
                **meta,
            }
            print(" OK")

        if q:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(q, f, indent=2, ensure_ascii=False)

    if meta_db:
        with open(METADATA_OUT, "w", encoding="utf-8") as f:
            json.dump(meta_db, f, indent=2, ensure_ascii=False)
        print(f"\n✨ {len(meta_db)} descrições salvas em {METADATA_OUT}")


def main():
    print("🚀 Iniciando Agente (V155 - Slugs Descritivos)...")
    try:
        segments = load_transcript()
    except:
        print("❌ Transcrição não encontrada.")
        return

    data = call_llm_for_structure(segments)
    if not data.get("topics"):
        print("⚠️ Sem tópicos.")
        return

    topics = process_structure(segments, data)
    export_results(topics, segments)
    print("✅ Finalizado.")


if __name__ == "__main__":
    main()
