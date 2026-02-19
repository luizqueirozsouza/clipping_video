import json
import os
import re
import sys

from dotenv import load_dotenv

# Adiciona o diretório atual ao path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import llm_service

load_dotenv(override=True)

# Configurações
ORIGINAL_URL = os.getenv("ORIGINAL_URL", "[LINK_DO_VÍDEO_AQUI]")
CHANNEL_TYPE = os.getenv("CHANNEL_TYPE", "christian").lower()
MAX_DESC_LENGTH = 2200

# Mapa global de transcrições (carregado via fallback)
TRANSCRIPTS_MAP = {}

# ==============================================================================
# PROMPTS
# ==============================================================================
PERSONAS = {
    "christian": """
    Você é um redator profissional de descrições para YouTube e Instagram, especializado em conteúdo cristão evangelístico no estilo Way of the Master (Ray Comfort).

    TAREFA:
    A partir da transcrição abaixo, crie uma descrição completa, impactante e profunda para este vídeo.

    REGRAS OBRIGATÓRIAS:
    1. NÃO copie a transcrição literalmente.
    2. Descaracterize termos sensíveis para evitar filtros.
    3. PROFUNDIDADE: Mesmo para vídeos curtos, escreva um texto denso e espiritual.
    4. Siga a estrutura abaixo rigorosamente.
    5. LIMITE DE CARACTERES: A descrição total DEVE ter no máximo 2100 caracteres.

    ESTRUTURA:
    1️⃣ Abertura (Pergunta reflexiva sobre eternidade/pecado).
    2️⃣ Contexto (O conflito moral ou o tema do vídeo).
    3️⃣ Ensinamento (A Lei de Deus, 10 Mandamentos, santidade).
    4️⃣ Evangelho (Jesus Cristo, a cruz, arrependimento e graça).
    5️⃣ Reflexão Final ("Se você morresse hoje...").
    6️⃣ CTA (Curta, Comente, Compartilhe).

    DADOS DO VÍDEO:
    - Título: {title}
    - Transcrição: "{text}"
    
    SAÍDA (JSON):
    {{
        "description": "Texto completo aqui (NÃO inclua o link aqui, ele será adicionado automaticamente)...",
        "hashtags": "#tags"
    }}
    """,
    "default": """
    Crie uma descrição completa para o vídeo.
    Limite: 2100 caracteres.
    Transcrição: {text}
    """,
}


def robust_json_clean(text):
    if not text:
        return "{}"
    text = text.strip()
    if "```" in text:
        text = re.sub(r"```[a-zA-Z]*\n?|```", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text


def load_transcripts_map():
    global TRANSCRIPTS_MAP
    print("🔍 Procurando transcrições em arquivos locais...")
    try:
        segments_path = "transcript_segments.json"
        cuts_files = [
            "vertical_cuts.json",
            "shorts_cuts.json",
            "youtube_cuts.json",
            "tiktok_cuts.json",
        ]

        if not os.path.exists(segments_path):
            return

        with open(segments_path, "r", encoding="utf-8") as f:
            segments = json.load(f)

        count = 0
        for cuts_path in cuts_files:
            if os.path.exists(cuts_path):
                print(f"   📖 Lendo cortes de {cuts_path}...")
                with open(cuts_path, "r", encoding="utf-8") as f:
                    cuts = json.load(f)
                    for cut in cuts:
                        slug = cut.get("slug") or cut.get("title")
                        start = cut.get("start")
                        end = cut.get("end")
                        if slug and start is not None and end is not None:
                            text = " ".join(
                                [
                                    s["text"]
                                    for s in segments
                                    if s["end"] > start and s["start"] < end
                                ]
                            )
                            if len(text) < 50 and cut.get("summary"):
                                text = f"{cut.get('summary')} {text}"
                            TRANSCRIPTS_MAP[slug] = text
                            count += 1
        print(f"✅ Mapa carregado com {count} referências.")
    except Exception as e:
        print(f"⚠️ Erro ao carregar transcrições: {e}")


def generate_description(text, title):
    persona = PERSONAS.get(CHANNEL_TYPE, PERSONAS.get("christian", PERSONAS["default"]))
    try:
        prompt_text = persona.format(title=title, text=text.replace("\n", " ")[:3000])
    except Exception:
        return None

    try:
        response = llm_service.call_llm(prompt_text, json_mode=True, temperature=0.3)
        if not response:
            return None

        clean_resp = robust_json_clean(response)
        data = json.loads(clean_resp, strict=False)

        desc = data.get("description", "").strip()
        tags = data.get("hashtags", "").strip()
        if not desc:
            return None

        footer = f"\n\n{tags}\n\n🎥 Vídeo Original: {ORIGINAL_URL}"
        if len(desc) + len(footer) > MAX_DESC_LENGTH:
            allowed = MAX_DESC_LENGTH - len(footer) - 50
            desc = desc[:allowed]
            if "." in desc:
                desc = desc.rsplit(".", 1)[0] + "."
            data["description"] = desc
        return data
    except Exception as e:
        print(f"      ❌ Erro: {e}")
        return None


def process_file_dict(json_filename):
    if not os.path.exists(json_filename):
        return
    print(f"📂 Processando {json_filename}...")
    with open(json_filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated_count = 0
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
        text_source = (
            item.get("text") or item.get("transcript") or item.get("raw_transcript")
        )
        if not text_source and key in TRANSCRIPTS_MAP:
            text_source = TRANSCRIPTS_MAP[key]

        title = item.get("youtube_title") or item.get("title") or key
        current_desc = item.get("youtube_description") or item.get("description") or ""

        generic_p = [
            "assista a este trecho",
            "[insira o link",
            "vídeo interessante sobre",
            "[link_do_vídeo_aqui]",
        ]
        is_generic = any(p in current_desc.lower() for p in generic_p)
        is_too_short = len(current_desc) < 200
        is_too_long = len(current_desc) > MAX_DESC_LENGTH

        if not text_source:
            continue

        if is_generic or is_too_short or not current_desc or is_too_long:
            r = (
                "Genérica"
                if is_generic
                else (
                    "Curta" if is_too_short else ("Longa" if is_too_long else "Vazia")
                )
            )
            print(f"   ✍️  {r}: {title[:40]}...")

            ai_data = generate_description(text_source, title)
            if ai_data:
                desc_f = ai_data.get("description", "")
                tags_f = ai_data.get("hashtags", "")
                final_text = (
                    f"{desc_f}\n\n{tags_f}\n\n🎥 Vídeo Original: {ORIGINAL_URL}"
                )

                if len(final_text) > MAX_DESC_LENGTH:
                    final_text = final_text[: MAX_DESC_LENGTH - 3] + "..."

                item["youtube_description"] = final_text
                updated_count += 1
                if updated_count % 3 == 0:
                    with open(json_filename, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                print("      ⚠️ Falhou")

    if updated_count > 0:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ {updated_count} atualizações em {json_filename}")


def process_file_list(json_filename):
    if not os.path.exists(json_filename):
        return
    print(f"📂 Processando {json_filename} (Lista)...")
    with open(json_filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated_count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug") or item.get("title")
        text_source = item.get("text") or item.get("transcript")
        if not text_source and slug in TRANSCRIPTS_MAP:
            text_source = TRANSCRIPTS_MAP[slug]

        title = item.get("youtube_title") or item.get("title") or "Vídeo"
        current_desc = item.get("description") or item.get("youtube_description") or ""
        generic_p = [
            "assista a este trecho",
            "[insira o link",
            "vídeo interessante sobre",
        ]

        if text_source and (
            any(p in current_desc.lower() for p in generic_p)
            or len(current_desc) < 200
            or len(current_desc) > MAX_DESC_LENGTH
        ):
            print(f"   ✍️  {title[:40]}...")
            ai_data = generate_description(text_source, title)
            if ai_data:
                item["description"] = ai_data.get("description", "")
                item["hashtags"] = ai_data.get("hashtags", "")
                f_text = f"{item['description']}\n\n{item['hashtags']}\n\n🎥 Vídeo Original: {ORIGINAL_URL}"
                if len(f_text) > MAX_DESC_LENGTH:
                    f_text = f_text[: MAX_DESC_LENGTH - 3] + "..."
                item["youtube_description"] = f_text
                updated_count += 1
                if updated_count % 3 == 0:
                    with open(json_filename, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)

    if updated_count > 0:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ {updated_count} atualizações.")


def main():
    print("🚀 AGENTE DE DESCRIÇÃO V176 (Final Fix: strict=False & formatting)")
    load_transcripts_map()
    files = [
        "video_descriptions.json",
        "tiktok_cuts.json",
        "shorts_cuts.json",
        "vertical_cuts.json",
        "youtube_cuts.json",
    ]
    for f in files:
        if os.path.exists(f):
            try:
                with open(f, "r", encoding="utf-8") as j:
                    d = json.load(j)
                    if isinstance(d, dict):
                        process_file_dict(f)
                    elif isinstance(d, list):
                        process_file_list(f)
            except Exception as e:
                print(f"Erro em {f}: {e}")


if __name__ == "__main__":
    main()
