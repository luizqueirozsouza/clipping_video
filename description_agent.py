import json
import os
import sys
from dotenv import load_dotenv

# Adiciona o diretório atual ao path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import llm_service

load_dotenv(override=True)

ORIGINAL_URL = os.getenv("ORIGINAL_URL", "[LINK_DO_VÍDEO_AQUI]")
CHANNEL_TYPE = os.getenv("CHANNEL_TYPE", "christian").lower()

# ==============================================================================
# PROMPTS (RESTAURADO E ROBUSTO)
# ==============================================================================
PERSONAS = {
    "christian": """
    Você é um redator profissional de descrições para YouTube, especializado em conteúdo cristão evangelístico, no estilo Way of the Master (Ray Comfort).

    TAREFA:
    A partir da transcrição abaixo, crie uma descrição completa e profunda para este vídeo.

    REGRAS OBRIGATÓRIAS:
    1. NÃO copie a transcrição.
    2. Descaracterize termos sensíveis.
    3. MÍNIMO DE 150 PALAVRAS: Seja detalhado e profundo.
    4. Siga a estrutura abaixo rigorosamente.

    ESTRUTURA:
    1️⃣ Abertura (Pergunta reflexiva sobre eternidade/pecado).
    2️⃣ Contexto (O confronto moral).
    3️⃣ Ensinamento (Lei de Deus, 10 Mandamentos).
    4️⃣ Evangelho (Jesus, arrependimento, graça).
    5️⃣ Reflexão ("Se morresse hoje...").
    6️⃣ CTA (Curta, Comente, Compartilhe).
    7️⃣ Link: {url}

    DADOS DO VÍDEO:
    - Título: {title}
    - Transcrição: "{text}"
    
    SAÍDA (JSON):
    {{
        "description": "Texto completo aqui...",
        "hashtags": "#tags"
    }}
    """,
    # (Mantenha as outras personas se precisar, aqui focamos no Christian)
}

def generate_description(text, title):
    """Gera descrição com verificação de qualidade."""
    
    # Prompt Selecionado
    persona = PERSONAS.get(CHANNEL_TYPE, PERSONAS["christian"])
    
    # Formata o prompt (Substitui as variáveis)
    try:
        # Pega os primeiros 2500 caracteres para não estourar o limite
        prompt_text = persona.format(
            url=ORIGINAL_URL, 
            title=title, 
            text=text.replace("\n", " ")[:2500]
        )
    except Exception as e:
        print(f"Erro ao formatar prompt: {e}")
        return None

    # Tenta gerar
    try:
        response = llm_service.call_llm(prompt_text, json_mode=True, temperature=0.3)
        clean_resp = llm_service.clean_json_response(response)
        data = json.loads(clean_resp)
        
        desc = data.get("description", "")
        
        # Verifica se a IA foi preguiçosa (texto muito curto)
        if len(desc) < 100:
            print(f"      ⚠️ IA gerou texto curto, tentando forçar qualidade...")
            # Adiciona uma instrução de reforço e tenta resumir/transcrever melhor
            # (Em produção real, poderíamos fazer um retry loop, aqui vamos retornar o que temos ou um fallback melhorado)
            
        return data

    except Exception as e:
        print(f"      ❌ Erro na geração: {e}")
        return None

def process_file_dict(json_filename):
    """
    Processa arquivos no formato Dicionário (igual ao seu video_descriptions.json).
    Ex: { "id": { "title": "...", "text": "..." } }
    """
    if not os.path.exists(json_filename):
        print(f"⚠️ Arquivo {json_filename} não encontrado.")
        return

    print(f"📂 Processando {json_filename} (Modo Dicionário)...")

    with open(json_filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print("   ⚠️ Este arquivo não parece estar no formato Dicionário (chave: valor). Tentando modo lista...")
        process_file_list(json_filename)
        return

    updated_count = 0
    
    # Itera sobre os valores do dicionário
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
            
        # Tenta encontrar o texto (transcript) em vários campos possíveis
        # O arquivo enviado não tinha 'text', mas talvez o original tenha
        text_source = item.get("text") or item.get("transcript") or item.get("raw_transcript")
        
        # Pega o título
        title = item.get("youtube_title") or item.get("title") or item.get("hook") or "Vídeo"
        
        # Pega a descrição atual
        current_desc = item.get("youtube_description") or item.get("description") or ""

        # CONDIÇÃO DE GERAÇÃO:
        # 1. Temos texto fonte? (Se não tiver, avisa e pula)
        # 2. A descrição atual é muito curta (< 150 chars) OU contém placeholder genérico?
        
        needs_update = False
        
        if not text_source:
            print(f"   ⚠️ Pulando '{key}': Nenhum texto de transcrição encontrado para gerar a descrição.")
            continue

        # Verifica se a descrição atual é "ruim"
        generic_phrases = ["assista a este trecho", "[insira o link", "vídeo interessante sobre"]
        is_generic = any(phrase in current_desc.lower() for phrase in generic_phrases)
        is_too_short = len(current_desc) < 150

        if is_generic or is_too_short or not current_desc:
            needs_update = True

        if needs_update:
            print(f"   ✍️  Gerando nova descrição para: {title[:40]}...")
            
            ai_data = generate_description(text_source, title)
            
            if ai_data:
                # Atualiza o campo correto (youtube_description ou description)
                desc_final = ai_data.get("description", "")
                tags_final = ai_data.get("hashtags", "")
                
                # Salva no campo que o arquivo usa (youtube_description)
                item["youtube_description"] = f"{desc_final}\n\n{tags_final}"
                
                # Opcional: Atualizar tags se o arquivo tiver lista de tags
                if "tags" in item and isinstance(item["tags"], list):
                    # Converte string de hashtags "#a #b" para lista ["a", "b"]
                    new_tags = [t.strip().replace("#","") for t in tags_final.split() if t.strip()]
                    item["tags"] = list(set(item["tags"] + new_tags)) # Junta sem duplicar

                updated_count += 1
            else:
                print(f"      ❌ Falha ao gerar para {key}")

    # Salva de volta
    if updated_count > 0:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ {updated_count} entradas atualizadas em {json_filename}")
    else:
        print(f"ℹ️ Nenhuma atualização necessária ou possível (sem transcrição).")

def process_file_list(json_filename):
    """
    Processa arquivos no formato Lista (padrão do pipeline anterior).
    Ex: [ { "title": "...", "text": "..." } ]
    """
    if not os.path.exists(json_filename):
        return

    print(f"📂 Processando {json_filename} (Modo Lista)...")

    with open(json_filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("   Formato desconhecido.")
        return

    updated_count = 0
    for item in data:
        if not isinstance(item, dict): continue

        text_source = item.get("text") or item.get("transcript")
        title = item.get("youtube_title") or item.get("title") or item.get("hook") or "Vídeo"
        current_desc = item.get("description") or item.get("youtube_description") or ""
        
        generic_phrases = ["assista a este trecho", "[insira o link", "vídeo interessante sobre"]
        is_generic = any(phrase in current_desc.lower() for phrase in generic_phrases)
        is_too_short = len(current_desc) < 150
        
        if text_source and (is_generic or is_too_short or not current_desc):
            print(f"   ✍️  {title[:40]}...")
            ai_data = generate_description(text_source, title)
            
            if ai_data:
                item["description"] = ai_data.get("description", "")
                item["hashtags"] = ai_data.get("hashtags", "")
                item["full_description_text"] = f"{item['description']}\n\n{item['hashtags']}"
                updated_count += 1

    if updated_count > 0:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ {updated_count} itens atualizados.")

def main():
    print(f"🚀 AGENTE DE DESCRICAO V173 (Fix para Dicionários e Descrições Curtas)")
    
    # Lista de arquivos para verificar
    # Adicionei explicitamente o video_descriptions.json que você enviou
    files = [
        "video_descriptions.json", 
        "tiktok_cuts.json",
        "shorts_cuts.json",
        "vertical_cuts.json"
    ]
    
    for fname in files:
        # Tenta detectar se é lista ou dicionário abrindo o arquivo
        if os.path.exists(fname):
            with open(fname, "r", encoding="utf-8") as f:
                try:
                    temp_data = json.load(f)
                    if isinstance(temp_data, dict):
                        process_file_dict(fname)
                    elif isinstance(temp_data, list):
                        process_file_list(fname)
                except Exception as e:
                    print(f"Erro ao ler {fname}: {e}")
        elif os.path.exists(f"outputs/{fname}"):
             # Repete lógica para pasta outputs se necessário
             pass

if __name__ == "__main__":
    main()