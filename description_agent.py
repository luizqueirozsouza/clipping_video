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
MAX_DESC_LENGTH = 2000

# Mapa global de transcrições (carregado via fallback)
TRANSCRIPTS_MAP = {}

# ==============================================================================
# PROMPTS
# ==============================================================================
PERSONAS = {
    "christian": """
    Você é um redator especializado em apologética e evangelismo bíblico (Estilo Ray Comfort e Todd Friel).
    
    SUA MISSÃO: Criar uma descrição ÚNICA para o clipe abaixo, organizada e visualmente atraente.
    
    REGRAS DE OURO:
    1. ORGANIZAÇÃO: Divida o texto em 3 parágrafos claros (Introdução ao assunto, Aplicação da Lei, Mensagem do Evangelho). Use quebras de linha entre eles.
    2. EMOJIS: Use emojis de forma estratégica para pontuar e dar vida ao texto (ex: ⚖️, 🙏, ✝️, 🏢, 🤔). Não exagere, mantenha a seriedade.
    3. ABERTURA ESPECÍFICA: Comece citando o assunto EXATO do vídeo (ex: "Neste vídeo sobre {title}...").
    4. ANALOGIA REAL: Use apenas analogias presentes no texto fornecido.
    5. FLUXO: Gancho do clipe -> Lei de Deus (Pecado) -> Evangelho (Cristo/Fé/Arrependimento).
    6. LIMITE: {limit} caracteres.
    
    CONTEÚDO PARA TRABALHAR:
    Título: {title}
    {text}
    """,
    "politics": """
    Você é um analista político sênior. 
    TAREFA: Crie uma descrição analítica sobre {title} baseada no conteúdo abaixo.
    1. Texto Fluido: Sem bullets.
    2. LIMITE: {limit} caracteres.
    {text}
    """,
    "bitcoin": """
    Você é um especialista em criptomoedas. 
    TAREFA: Crie uma descrição sobre {title} baseada no conteúdo abaixo.
    1. LIMITE: {limit} caracteres.
    {text}
    """,
    "economy": """
    Você é um economista focado em liberdade. 
    TAREFA: Crie uma descrição didática sobre {title} baseada no conteúdo abaixo.
    1. LIMITE: {limit} caracteres.
    {text}
    """,
    "aesthetics": """
    Você é um redator profissional de estética e beleza.
    TAREFA: Crie uma descrição informativa sobre {title} baseada no conteúdo abaixo.
    1. LIMITE: {limit} caracteres.
    {text}
    """,
    "default": """
    Crie uma descrição informativa para o vídeo {title} baseada no conteúdo abaixo.
    1. LIMITE: {limit} caracteres.
    {text}
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
                            # Une o resumo à transcrição para dar contexto rico ao LLM
                            summary = cut.get("summary", "")
                            full_context = f"RESUMO DO CONTEÚDO: {summary}\n\nTRANSCRIÇÃO BRUTA: {text}"
                            TRANSCRIPTS_MAP[slug] = full_context
                            count += 1
        print(f"✅ Mapa carregado com {count} referências.")
    except Exception as e:
        print(f"⚠️ Erro ao carregar transcrições: {e}")


def generate_description(text, title):
    persona_template = PERSONAS.get(
        CHANNEL_TYPE, PERSONAS.get("christian", PERSONAS["default"])
    )

    # Log de depuração para verificar o que está sendo enviado
    clean_text_log = text[:100].replace("\n", " ")
    print(f"      📝 Processando: {title[:30]}... | Transcrição: {clean_text_log}...")

    # Calcula overhead (hashtags estimadas + URL + quebras de linha)
    url_footer = f"\n\n🎥 Vídeo Original: {ORIGINAL_URL}"
    # Reservamos 300 caracteres para hashtags e margem de segurança
    reserved = len(url_footer) + 300
    available_limit = MAX_DESC_LENGTH - reserved
    if available_limit < 500:
        available_limit = 500  # Segurança mínima

    try:
        # Passa o limite dinâmico para o prompt
        prompt_text = persona_template.format(
            title=title, text=text.replace("\n", " ")[:3000], limit=available_limit
        )
    except Exception as e:
        print(f"      ⚠️ Erro format prompt: {e}")
        return None

    try:
        response = llm_service.call_llm(prompt_text, json_mode=True, temperature=0.3)
        if not response:
            return None

        clean_resp = robust_json_clean(response)
        data = json.loads(clean_resp, strict=False)

        # Se o LLM retornar uma lista, pega o primeiro item
        if isinstance(data, list) and len(data) > 0:
            data = data[0]

        if not isinstance(data, dict):
            return None

        desc = data.get("description", "").strip()
        tags = data.get("hashtags", "").strip()
        if not desc:
            return None

        # Monta o final_text para validar comprimento real
        footer = f"\n\n{tags}{url_footer}"
        total_len = len(desc) + len(footer)

        if total_len > MAX_DESC_LENGTH:
            allowed = MAX_DESC_LENGTH - len(footer) - 10
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
        is_too_short = len(current_desc) < 250
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
                url_footer = f"\n\n🎥 Vídeo Original: {ORIGINAL_URL}"
                final_text = f"{desc_f}\n\n{tags_f}{url_footer}"

                if len(final_text) > MAX_DESC_LENGTH:
                    # Se ainda passar, corta preservando o footer
                    footer_len = len(tags_f) + len(url_footer) + 2
                    allowed = MAX_DESC_LENGTH - footer_len - 5
                    final_text = desc_f[:allowed] + "...\n\n" + tags_f + url_footer

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
                desc_f = ai_data.get("description", "")
                tags_f = ai_data.get("hashtags", "")
                url_footer = f"\n\n🎥 Vídeo Original: {ORIGINAL_URL}"

                item["description"] = desc_f
                item["hashtags"] = tags_f

                f_text = f"{desc_f}\n\n{tags_f}{url_footer}"
                if len(f_text) > MAX_DESC_LENGTH:
                    footer_len = len(tags_f) + len(url_footer) + 2
                    allowed = MAX_DESC_LENGTH - footer_len - 5
                    f_text = desc_f[:allowed] + "...\n\n" + tags_f + url_footer

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
