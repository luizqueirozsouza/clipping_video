import ast
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

# ======================
# CONFIGURAÇÃO CENTRAL (V113 - HYBRID PARSER)
# ======================
DEFAULT_MODEL = "google/gemini-2.0-flash-001"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

OR_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_KEY}",
    "HTTP-Referer": "http://localhost:8501",
    "X-Title": "AI Video Slicer V113",
    "Content-Type": "application/json",
}


def log(msg, level="info"):
    icon = "ℹ️"
    if level == "error":
        icon = "❌"
    elif level == "success":
        icon = "✅"
    elif level == "warning":
        icon = "⚠️"
    print(f"[{time.strftime('%H:%M:%S')}] {icon} {msg}")


def try_fix_json(text):
    """
    Tenta salvar um JSON quebrado usando múltiplas estratégias.
    """
    # 1. Estratégia AST (Interpreta como Python Dict/List)
    # Python aceita {key: 'value'}, {'key': 'value'}, e vírgulas extras.
    try:
        # Converte null/true/false do JSON para None/True/False do Python
        py_text = (
            text.replace("null", "None")
            .replace("true", "True")
            .replace("false", "False")
        )
        obj = ast.literal_eval(py_text)
        return json.dumps(obj)  # Retorna JSON limpo e válido
    except:
        pass

    # 2. Estratégia Regex (Conserta chaves sem aspas)
    # Ex: { start: 10 } -> { "start": 10 }
    text = re.sub(r"(?<=\{|\,)\s*([a-zA-Z0-9_]+)\s*:", r'"\1":', text)

    # 3. Limpeza de Vírgulas Fantasmas
    text = re.sub(r",\s*]", "]", text)
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r"}\s*{", "}, {", text)  # Objeto colado

    return text


def clean_json_response(text):
    if not text:
        return ""
    text = text.strip()
    if "```" in text:
        text = re.sub(r"```json|```", "", text).strip()

    # Tenta isolar a lista principal [ ... ]
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    except:
        pass

    return text


def call_llm(
    prompt_user,
    prompt_system="You are a helpful assistant.",
    json_mode=False,
    temperature=0.7,
    max_tokens=100000,
):
    # 1. OPENROUTER
    if OPENROUTER_KEY:
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and ("gpt" in OPENROUTER_MODEL or "flash" in OPENROUTER_MODEL):
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(4):
            try:
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=OR_HEADERS,
                    data=json.dumps(payload),
                    timeout=120,
                )
                if resp.status_code == 200:
                    try:
                        content = resp.json()["choices"][0]["message"]["content"]
                        if not content:
                            raise ValueError("Vazio")
                        return content
                    except:
                        log("⚠️ Erro parsing resposta OpenRouter", "error")
                elif resp.status_code == 429:
                    wt = 5 * (2**attempt)
                    log(f"⏳ Rate Limit. Aguardando {wt}s...", "warning")
                    time.sleep(wt)
                    if attempt >= 1 and GEMINI_KEY:  # Failover
                        log("⚠️ OpenRouter lento. Tentando Direct...", "warning")
                        break
                else:
                    if resp.status_code < 500:
                        break
            except Exception as e:
                log(f"⚠️ Erro Conexão (T{attempt + 1}): {e}", "error")
                time.sleep(2)

    # 2. GEMINI DIRECT
    if GEMINI_KEY:
        try:
            from google import genai

            client = genai.Client(api_key=GEMINI_KEY)
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"{prompt_system}\n\nTASK:\n{prompt_user}",
                config={"response_mime_type": "application/json"} if json_mode else {},
            )
            return response.text
        except Exception as e:
            log(f"⚠️ Erro Gemini Direct: {e}", "error")

    # 3. OPENAI DIRECT (Fallback)
    if OPENAI_KEY:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": prompt_user},
                ],
                response_format={"type": "json_object"} if json_mode else None,
            )
            return response.choices[0].message.content
        except:
            pass

    return None


def correct_transcript_grammar(segments):
    """
    Corrige a gramática processando em pequenos lotes (BATCHES) para evitar
    que o LLM mescle segmentos (ex: 652 virando 429 linhas).
    """
    if not segments:
        return segments

    print(f"✍️  Iniciando correção gramatical ({len(segments)} segmentos)...")

    BATCH_SIZE = (
        20  # Processa 20 linhas por vez (equilíbrio entre velocidade e precisão)
    )
    total_batches = (len(segments) + BATCH_SIZE - 1) // BATCH_SIZE

    prompt_system = "Você é um editor de texto especialista. Corrija a ortografia e gramática das frases listadas abaixo. **RETORNE APENAS UMA LISTA JSON**. É CRUCIAL que você mantenha a MESMA quantidade de linhas e a MESMA ordem. Não mescle frases, mesmo que pareça incompleto."

    corrected_count = 0

    for i in range(0, len(segments), BATCH_SIZE):
        batch = segments[i : i + BATCH_SIZE]
        batch_index = i // BATCH_SIZE + 1

        # Prepara o texto deste lote com índices para ajudar a manter a ordem
        batch_text = "\n".join([f"[{j}] {seg['text']}" for j, seg in enumerate(batch)])

        prompt_user = (
            f"Corrija a lista abaixo (Lote {batch_index}/{total_batches}).\n"
            f"Retorne um array JSON contendo exatamente {len(batch)} strings corrigidas.\n\n"
            f"{batch_text}"
        )

        try:
            # Chama a LLM
            response = call_llm(
                prompt_user, prompt_system, json_mode=True, temperature=0.2
            )

            if response:
                clean_json = clean_json_response(response)
                corrected_list = json.loads(clean_json)

                # VERIFICAÇÃO CRÍTICA DE INTEGRIDADE
                if isinstance(corrected_list, list) and len(corrected_list) == len(
                    batch
                ):
                    # Atualiza os segmentos originais
                    for j, seg in enumerate(batch):
                        original_text = seg["text"]
                        corrected_text = corrected_list[j]

                        # Opcional: Só atualiza se houver mudança significativa para manter o cache
                        if original_text != corrected_text:
                            seg["text"] = corrected_text

                    corrected_count += len(batch)
                    print(f"   ✅ Lote {batch_index}/{total_batches} corrigido.")
                else:
                    print(
                        f"   ⚠️  Lote {batch_index}/{total_batches} com erro de tamanho ({len(corrected_list)} vs {len(batch)}). Mantendo original."
                    )

        except Exception as e:
            print(
                f"   ❌ Erro no lote {batch_index}: {e}. Mantendo original deste lote."
            )
            continue

    print(
        f"✅ Processo finalizado. {corrected_count}/{len(segments)} segmentos processados."
    )
    return segments
