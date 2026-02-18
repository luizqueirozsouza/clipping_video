import json
import os
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import llm_service
from dotenv import load_dotenv

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
# === Lê o tipo de canal escolhido no App (Padrão: christian) ===
CHANNEL_TYPE = os.getenv("CHANNEL_TYPE", "christian").lower()

# Regras de Tempo (Segundos)
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

# ==============================================================================
# CONTEXTOS & TEMPLATES (V173 - FIX SHORTS)
# ==============================================================================

TEMPLATES = {
    # === 1. CRISTÃO (Way of the Master - FIX PARA SHORTS) ===
    "christian": """
    Você é um redator teológico sênior, especializado em evangelismo estilo Way of the Master (Ray Comfort).
    
    CRUCIAL PARA SHORTS/REELS:
    Mesmo que o vídeo seja curto, a descrição deve ser UM MINI-ARTIGO TEOLÓGICO COMPLETO.
    Não escreva resumos curtos. O algoritmo do YouTube/TikTok favorece descrições longas que retêm a leitura.
    
    ESTRUTURA OBRIGATÓRIA (PREENCHA TODOS OS CAMPOS COM PROFUNDIDADE):

    1️⃣ Abertura Impactante
    Comece com uma pergunta ou afirmação que prenda a atenção sobre: eternidade, julgamento ou verdade.

    2️⃣ O Problema (A Lei)
    Explique o que está acontecendo no vídeo. Se o vídeo for curto e não explicar tudo, USE SEU CONHECIMENTO para contextualizar que quebrar a Lei de Deus (mentira, roubo, cobiça) traz condenação.

    3️⃣ A Solução (O Evangelho)
    Mesmo que o vídeo corte antes de falar de Jesus, VOCÊ DEVE PREGAR O EVANGELHO AQUI. Fale da Cruz, do Sangue e do Arrependimento.

    4️⃣ Reflexão Pessoal
    "Se você morresse hoje..." (Gere reflexão profunda).

    5️⃣ CTA (Engajamento)
    Convites claros para Like, Comentário e Inscrição.

    6️⃣ Link Original
    👉 Assista ao vídeo completo aqui: [LINK_ORIGINAL]
    """,

    # === 2. POLÍTICA ===
    "politics": """
    IDENTIDADE: Análise Política e Debate.
    ESTILO: Analítico, Crítico, Investigativo.
    
    PARA SHORTS: Não seja raso. Aprofunde a análise no texto mesmo que o vídeo seja rápido.

    TEMPLATE OBRIGATÓRIO:
    🔥 [FRASE POLÊMICA/GANCHO]
    Neste vídeo, analisamos [TEMA CENTRAL]:
    • [Fato 1]
    • [Fato 2]
    • [A contradição revelada]
    🔍 A Análise: [Realidade vs Narrativa - Escreva 2 parágrafos densos aqui]
    💬 Pergunta de Debate: [Pergunta para gerar comentários]
    📢 CTA: Se inscreva.
    👉 Link original: [LINK_ORIGINAL]
    """,

    # === 3. FLAMENGO ===
    "flamengo": """
    IDENTIDADE: Notícias do Flamengo (Mengão).
    ESTILO: Apaixonado, Urgente, Clubista.

    TEMPLATE OBRIGATÓRIO:
    🔴⚫ [MANCHETE URGENTE]
    
    O que aconteceu:
    [Explique a notícia com detalhes, mesmo que o vídeo seja rápido]

    🏟️ Opinião:
    [Análise torcedora sobre o impacto no time]

    💬 Fala, Nação!: [Pergunta]
    👉 Vídeo completo: [LINK_ORIGINAL]
    """,

    # === 4. BITCOIN ===
    "bitcoin": """
    IDENTIDADE: Criptomoedas e Liberdade.
    ESTILO: Visionário, Técnico, "HODL".

    TEMPLATE OBRIGATÓRIO:
    🚀 [ALERTA DE MERCADO]

    Análise Técnica/Fundamentalista:
    [Explique o movimento do gráfico ou a notícia macro]
    [Dê contexto sobre o ciclo do Bitcoin]

    💬 Estratégia: [Comprar ou Vender?]
    👉 Análise completa: [LINK_ORIGINAL]
    """,

    # === 5. ECONOMIA ===
    "economy": """
    IDENTIDADE: Economia e Impostos.
    ESTILO: Educativo, "Cuide do seu bolso".

    TEMPLATE OBRIGATÓRIO:
    💸 [IMPACTO NO SEU BOLSO]

    Entenda a mudança:
    [Explique a lei/imposto detalhadamente]
    [Como isso afeta o poder de compra]

    💡 Proteção: [O que fazer?]
    👉 Vídeo original: [LINK_ORIGINAL]
    """
}

# Seleciona o template ativo
ACTIVE_TEMPLATE = TEMPLATES.get(CHANNEL_TYPE, TEMPLATES["christian"])

# =========================
# HELPERS
# =========================

def safe_json_parse(text: str) -> Dict:
    try: return json.loads(text)
    except: pass
    try:
        text = text.replace("```json", "").replace("```", "")
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1: return json.loads(text[start : end + 1])
    except: pass
    return {}

def slugify(text):
    if not text: return "corte-sem-nome"
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
    if not os.path.exists(TRANSCRIPT_PATH): raise FileNotFoundError(f"{TRANSCRIPT_PATH} não encontrado.")
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        for item in data: item['start'], item['end'] = float(item['start']), float(item['end'])
        return data

def get_text_segment(segments: List[Dict], start_time: float, end_time: float) -> str:
    return " ".join([s['text'] for s in segments if s['end'] > start_time and s['start'] < end_time])

def call_llm_for_structure(segments: List[Dict]) -> Dict:
    transcript_text = "\n".join(f"[ID {i}] {s['text']}" for i, s in enumerate(segments))
    prompt = build_topic_prompt(transcript_text, len(segments) - 1)
    print("⏳ Analisando estrutura narrativa via LLM...")
    response = llm_service.call_llm(prompt, json_mode=True, temperature=0.2)
    data = safe_json_parse(response)
    if not data:
        try: data = json.loads(llm_service.clean_json_response(response))
        except: return {"topics": []}
    return {"topics": data} if isinstance(data, list) else data

def generate_metadata_for_cut(cut_item: Dict, segments: List[Dict], format_type: str) -> Dict:
    full_text = get_text_segment(segments, cut_item['start'], cut_item['end'])[:5000]
    prompt = build_metadata_prompt(full_text, cut_item['duration'], format_type)
    
    try:
        response = llm_service.call_llm(prompt, json_mode=True, temperature=0.4)
        meta = safe_json_parse(response)
        if not meta: raise ValueError("JSON inválido")
        
        desc = meta.get("youtube_description", "")
        # Fallback de segurança para link
        if ORIGINAL_URL not in desc:
            desc += f"\n\n👉 Vídeo Original: {ORIGINAL_URL}"
        
        meta["youtube_description"] = desc
        return meta
    except:
        return {
            "youtube_title": cut_item['title'],
            "youtube_description": f"Assista a este trecho.\n\n🎥 Vídeo Original: {ORIGINAL_URL}",
            "tags": ["shorts", "video"]
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
                subs.append(Subtopic(st["title"], 0, 0, st_start, st_end, st.get("summary", ""), st_end - st_start))
            topics.append(Topic(t["title"], t_start, t_end, t.get("summary", ""), subs, t_end - t_start))
        except: continue
    return topics

def create_cut_item(title, start, end, duration, summary):
    safe_slug = slugify(title)
    return {
        "start": start, "end": end, "duration": duration, "title": title,
        "youtube_title": title, "summary": summary, "score": 100,
        "slug": safe_slug
    }

def export_results(topics: List[Topic], segments: List[Dict]):
    try:
        with open(TOPICS_OUT, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in topics], f, ensure_ascii=False, indent=2)
    except: pass

    shorts, longs = [], []
    print("\n🔍 SELECIONANDO CORTES:")

    for t in topics:
        if MIN_SHORT_SEC <= t.duration <= MAX_SHORT_SEC:
            print(f"   ✅ [Shorts] Tópico: '{t.title}' ({t.duration:.1f}s)")
            shorts.append(create_cut_item(t.title, t.start_time, t.end_time, t.duration, t.summary))
        elif t.duration > MAX_SHORT_SEC:
            for s in t.subtopics:
                if MIN_SHORT_SEC <= s.duration <= MAX_SHORT_SEC:
                    print(f"      🔹 [Shorts] Subtópico: '{s.title}' ({s.duration:.1f}s)")
                    shorts.append(create_cut_item(s.title, s.start_time, s.end_time, s.duration, s.summary))
        
        if MIN_LONG_SEC <= t.duration <= MAX_LONG_SEC:
            print(f"   📺 [YouTube] Longo: '{t.title}' ({t.duration:.1f}s)")
            longs.append(create_cut_item(t.title, t.start_time, t.end_time, t.duration, t.summary))

    print(f"\n✍️ GERANDO DESCRIÇÕES (ESTILO '{CHANNEL_TYPE.upper()}')...")
    meta_db = {}
    
    for q, label, out_file in [(shorts, "Short", SHORTS_OUT), (longs, "Longo", LONGS_OUT)]:
        for cut in q:
            print(f"   {label} ({cut['duration']:.0f}s): {cut['slug']}...", end="", flush=True)
            meta = generate_metadata_for_cut(cut, segments, label)
            cut["youtube_title"] = meta.get("youtube_title", cut["title"])
            meta_db[cut["slug"]] = {"type": label.lower(), "filename": f"{cut['slug']}.mp4", **meta}
            print(" OK")
        
        if q:
            with open(out_file, "w", encoding="utf-8") as f: json.dump(q, f, indent=2, ensure_ascii=False)

    if meta_db:
        with open(METADATA_OUT, "w", encoding="utf-8") as f: json.dump(meta_db, f, indent=2, ensure_ascii=False)
        print(f"\n✨ {len(meta_db)} descrições salvas em {METADATA_OUT}")

def main():
    print(f"🚀 Iniciando Agente (V173 - Fix Shorts Desc: {CHANNEL_TYPE.upper()})...")
    try: segments = load_transcript()
    except: print("❌ Transcrição não encontrada."); return
    
    data = call_llm_for_structure(segments)
    if not data.get("topics"): print("⚠️ Sem tópicos."); return
    
    topics = process_structure(segments, data)
    export_results(topics, segments)
    print("✅ Finalizado.")

if __name__ == "__main__":
    main()