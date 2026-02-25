import json
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from typing import Dict, List

from dotenv import load_dotenv
from tqdm import tqdm

import llm_service

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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
# === Lê o tipo de canal escolhido no App (Padrão: christian) ===
CHANNEL_TYPE = os.getenv("CHANNEL_TYPE", "christian").lower()

# Regras de Tempo (Segundos)
MIN_SHORT_SEC = 40
MAX_SHORT_SEC = 180
MIN_LONG_SEC = 180
MAX_LONG_SEC = 900

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


# ==============================================================================
# CONTEXTOS & TEMPLATES (V173 - FIX SHORTS)
# ==============================================================================

TEMPLATES = {
    # === 1. CRISTÃO (Way of the Master) ===
    "christian": """
    IDENTIDADE: Redator teológico sênior, especializado em evangelismo estilo Way of the Master (Ray Comfort).
    TAREFA: Escreva uma descrição profunda e impactante.
    REGRAS DE FORMATAÇÃO:
    - Texto Fluido: Escreva um texto corrido e envolvente. NÃO use títulos como "Abertura:", "Problema:", "Ensinamento:", "CTA:" ou numeração.
    - Integre organicamente: Comece com uma reflexão sobre eternidade/verdade, aborde o conflito moral (Lei), apresente o Evangelho (Jesus e Arrependimento) e termine com uma chamada para ação.
    - Profundidade: Escreva um texto denso e espiritual, mesmo para vídeos curtos.
    """,
    # === 2. POLÍTICA ===
    "politics": """
    IDENTIDADE: Análise Política e Debate.
    ESTILO: Analítico, Crítico, Investigativo.
    REGRAS DE FORMATAÇÃO:
    - Texto Fluido: NÃO use marcadores (bullets) ou títulos de seção.
    - Integre organicamente: Comece com um gancho polêmico, desenvolva a análise dos fatos e as contradições reveladas, e termine com uma pergunta para o debate e CTA.
    - Para Shorts: Aprofunde a análise no texto mesmo que o vídeo seja rápido.
    """,
    # === 3. FLAMENGO ===
    "flamengo": """
    IDENTIDADE: Notícias do Flamengo (Mengão).
    ESTILO: Apaixonado, Urgente, Clubista.
    REGRAS DE FORMATAÇÃO:
    - Texto Fluido: NÃO use títulos como "O que aconteceu:" ou "Opinião:". 
    - Integre organicamente: Comece com a manchete urgente, narre os detalhes da notícia, dê a opinião torcedora sobre o impacto no time e termine com uma pergunta para a Nação.
    """,
    # === 4. BITCOIN ===
    "bitcoin": """
    IDENTIDADE: Criptomoedas e Liberdade.
    ESTILO: Visionário, Técnico, "HODL".
    REGRAS DE FORMATAÇÃO:
    - Texto Fluido: NÃO use títulos de seção ou bullets.
    - Integre organicamente: Comece com um alerta de mercado, explique o movimento técnico ou notícia macro, fale sobre o ciclo do Bitcoin e termine com a estratégia recomendada.
    """,
    # === 5. ECONOMIA ===
    "economy": """
    IDENTIDADE: Economia e Impostos.
    ESTILO: Educativo, "Cuide do seu bolso".
    REGRAS DE FORMATAÇÃO:
    - Texto Fluido: NÃO use títulos de seção.
    - Integre organicamente: Comece pelo impacto no bolso do usuário, explique a mudança na lei/imposto detalhadamente e finalize com dicas de proteção e o que fazer.
    """,
}

# Seleciona o template ativo
ACTIVE_TEMPLATE = TEMPLATES.get(CHANNEL_TYPE, TEMPLATES["christian"])

# =========================
# HELPERS
# =========================


def safe_json_parse(text: str) -> Dict:
    try:
        return json.loads(text)
    except Exception:
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
Atue como editor de vídeo sênior. Estruture o conteúdo em TÓPICOS NARRATIVOS COMPLETOS.
REGRAS CRUCIALMENTE IMPORTANTES:
1. PONTOS DE CORTE: Identifique o início e o fim EXATOS de cada raciocínio. O fim deve ser uma conclusão natural de pensamento.
2. INTEGRIDADE: Dê preferência a tópicos que contenham a introdução, o desenvolvimento e a conclusão da ideia.
3. FORMATO: 
   - Shorts: Priorize 60 a 180 segundos.
   - YouTube Longo: Busque agrupar ideias correlatas para gerar vídeos de 5 a 12 minutos. Evite picotar demais o conteúdo longo.
FORMATO JSON PURO:
{{ "topics": [ {{ "title": "...", "summary": "...", "start_index": 0, "end_index": 15, "subtopics": [] }} ] }}
TRANSCRIÇÃO:
{transcript_excerpt}
"""


def build_metadata_prompt(video_text: str, duration: float, format_type: str) -> str:
    # Injeta a URL correta no template
    template_filled = ACTIVE_TEMPLATE.replace("[LINK_ORIGINAL]", ORIGINAL_URL)

    # === LÓGICA DE REFORÇO PARA SHORTS ===
    extra_instruction = ""
    if format_type == "Short" or duration < 120:
        extra_instruction = """
        ATENÇÃO: ESTE É UM VÍDEO CURTO (SHORT), MAS A DESCRIÇÃO DEVE SER LONGA.
        NÃO RESUMA. EXPANDA OS CONCEITOS TEOLÓGICOS/ANALÍTICOS.
        Use seu conhecimento externo para enriquecer o texto onde o vídeo for breve.
        """

    return f"""
{template_filled}

TAREFA: Escreva os metadados para este trecho de {duration:.0f}s.
TEXTO DO VÍDEO: "{video_text}"

{extra_instruction}

IMPORTANTE: 
1. Siga a ESTRUTURA OBRIGATÓRIA acima RIGOROSAMENTE.
2. Seja profundo e detalhado, evitando textos rasos.

RETORNE APENAS JSON:
{{
    "youtube_title": "Título Clickbait (Max 60 chars)",
    "youtube_description": "Texto completo da descrição seguindo a estrutura.",
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]
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
    print("Analisando estrutura narrativa via LLM...")
    response = llm_service.call_llm(prompt, json_mode=True, temperature=0.2)
    data = safe_json_parse(response)
    if not data:
        try:
            data = json.loads(llm_service.clean_json_response(response))
        except Exception:
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
        # Fallback de segurança para link
        if ORIGINAL_URL not in desc:
            desc += f"\n\n👉 Vídeo Original: {ORIGINAL_URL}"

        meta["youtube_description"] = desc
        return meta
    except Exception:
        return {
            "youtube_title": cut_item["title"],
            "youtube_description": f"Assista a este trecho.\n\n🎥 Vídeo Original: {ORIGINAL_URL}",
            "tags": ["shorts", "video"],
        }


def process_structure(segments: List[Dict], llm_data: Dict) -> List[Topic]:
    topics = []
    for t in llm_data.get("topics", []):
        try:
            idx_start = t.get("start_index", 0)
            idx_end = t.get("end_index", 0)

            # Garante que o índice final seja válido e tenta pegar o final real do segmento
            t_start = segments[idx_start]["start"]
            t_end = segments[idx_end]["end"]

            # Se o tópico termina muito perto do próximo, adicionamos uma pequena margem (0.5s)
            # para evitar cortes secos, se houver espaço na transcrição total
            if idx_end < len(segments) - 1:
                next_start = segments[idx_end + 1]["start"]
                if next_start - t_end > 0.1:
                    t_end = min(t_end + 0.8, next_start)

            subs = []
            for st in t.get("subtopics", []):
                s_idx_start = st.get("start_index", 0)
                s_idx_end = st.get("end_index", 0)
                st_start = segments[s_idx_start]["start"]
                st_end = segments[s_idx_end]["end"]

                # Margem também para subtópicos
                if s_idx_end < len(segments) - 1:
                    s_next_start = segments[s_idx_end + 1]["start"]
                    st_end = min(st_end + 0.5, s_next_start)

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
        except Exception as e:
            print(f"Aviso: Erro ao processar tópico: {e}")
            continue
    return topics


def create_cut_item(title, start, end, duration, summary):
    safe_slug = slugify(title)
    return {
        "start": start,
        "end": end,
        "duration": duration,
        "title": title,
        "youtube_title": title,
        "summary": summary,
        "score": 100,
        "slug": safe_slug,
    }


def export_results(topics: List[Topic], segments: List[Dict]):
    try:
        with open(TOPICS_OUT, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in topics], f, ensure_ascii=False, indent=2)
    except:
        pass

    shorts, longs = [], []
    print("\n SELECIONANDO CORTES:")

    for t in topics:
        # Prioridade máxima: Tópico inteiro se couber no limite do Short
        if MIN_SHORT_SEC <= t.duration <= MAX_SHORT_SEC:
            print(f"   [Shorts] Topico Inteiro: '{t.title}' ({t.duration:.1f}s)")
            shorts.append(
                create_cut_item(
                    t.title, t.start_time, t.end_time, t.duration, t.summary
                )
            )

        # Se for muito longo para short, mas tem subtópicos que cabem
        elif t.duration > MAX_SHORT_SEC:
            for s in t.subtopics:
                if MIN_SHORT_SEC <= s.duration <= MAX_SHORT_SEC:
                    print(f"      [Shorts] Subtopico: '{s.title}' ({s.duration:.1f}s)")
                    shorts.append(
                        create_cut_item(
                            s.title, s.start_time, s.end_time, s.duration, s.summary
                        )
                    )

        # Vídeos Longos: Prioriza o tópico completo sempre
        if MIN_LONG_SEC <= t.duration <= MAX_LONG_SEC:
            print(f"   [YouTube] Topico Completo: '{t.title}' ({t.duration:.1f}s)")
            longs.append(
                create_cut_item(
                    t.title, t.start_time, t.end_time, t.duration, t.summary
                )
            )
        elif t.duration > MAX_LONG_SEC:
            # Caso o tópico seja GIGANTE, podemos tentar fundir subtópicos ou usar o maior subtópico
            # Por enquanto, mantemos a lógica de segurança
            pass

    print(f"\nGERANDO DESCRICOES (ESTILO '{CHANNEL_TYPE.upper()}')...")
    meta_db = {}

    for q, label, out_file in [
        (shorts, "Short", SHORTS_OUT),
        (longs, "Longo", LONGS_OUT),
    ]:
        if not q:
            continue

        desc_text = f"Gerando descrições ({label})"
        with tqdm(q, desc=desc_text, unit="desc") as pbar:
            for cut in pbar:
                try:
                    meta = generate_metadata_for_cut(cut, segments, label)
                    cut["youtube_title"] = meta.get("youtube_title", cut["title"])
                    meta_db[cut["slug"]] = {
                        "type": label.lower(),
                        "filename": f"{cut['slug']}.mp4",
                        **meta,
                    }
                except Exception as e:
                    pbar.write(
                        f"[ERRO] Erro ao gerar metadados para {cut['slug']}: {e}"
                    )

        if q:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(q, f, indent=2, ensure_ascii=False)

    if meta_db:
        with open(METADATA_OUT, "w", encoding="utf-8") as f:
            json.dump(meta_db, f, indent=2, ensure_ascii=False)
        print(f"\n{len(meta_db)} descricoes salvas em {METADATA_OUT}")


def main():
    print(f"Iniciando Agente (V173 - Fix Shorts Desc: {CHANNEL_TYPE.upper()})...")
    try:
        segments = load_transcript()
    except Exception:
        print("Erro: Transcricao nao encontrada.")
        return

    data = call_llm_for_structure(segments)
    if not data.get("topics"):
        print("Aviso: Sem topicos.")
        return

    topics = process_structure(segments, data)
    export_results(topics, segments)
    print("Finalizado.")


if __name__ == "__main__":
    main()
