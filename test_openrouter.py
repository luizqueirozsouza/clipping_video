import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

# Carrega chaves do .env
load_dotenv(override=True)

print("=" * 40)
print("🔎 DIAGNÓSTICO DE CONEXÃO OPENROUTER")
print("=" * 40)

api_key = os.getenv("OPENROUTER_API_KEY")

if not api_key:
    print("❌ ERRO: Chave OPENROUTER_API_KEY não encontrada no arquivo .env")
    sys.exit(1)

print(f"🔑 Chave detectada: {api_key[:6]}...{api_key[-4:]}")
print("🌐 Tentando conectar em 'https://openrouter.ai/api/v1'...")

try:
    # Configura cliente
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        timeout=10.0,  # Timeout curto para teste
    )

    # Tenta chamada simples
    print("⏳ Enviando 'Olá' para o modelo 'google/gemini-2.0-flash-001'...")

    completion = client.chat.completions.create(
        model="anthropic/claude-3-haiku",
        messages=[{"role": "user", "content": "Responda apenas: 'Conexão OK!'"}],
    )

    response = completion.choices[0].message.content
    print("\n✅ SUCESSO! A API respondeu:")
    print(f"   🤖 '{response}'")

except Exception as e:
    print("\n❌ FALHA NA CONEXÃO!")
    print(f"   Tipo do Erro: {type(e).__name__}")
    print(f"   Mensagem: {e}")

    # Dicas baseadas no erro
    error_str = str(e).lower()
    print("\n💡 DICA DE SOLUÇÃO:")
    if "401" in error_str:
        print(
            "   -> Sua chave está INCORRETA ou expirada. Gere uma nova no site openrouter.ai"
        )
    elif "402" in error_str:
        print("   -> Falta de CRÉDITOS na conta OpenRouter.")
    elif "connect" in error_str or "timeout" in error_str:
        print(
            "   -> Bloqueio de REDE. Seu PC não consegue chegar no site da OpenRouter."
        )
        print("      1. Desligue VPNs.")
        print("      2. Mude seu DNS para 8.8.8.8 (Google).")
        print("      3. Tente reiniciar o modem.")
    elif "404" in error_str:
        print(
            "   -> Modelo não encontrado. O modelo 'google/gemini-2.0-flash-001' pode ter mudado de nome."
        )

print("=" * 40)
input("Pressione ENTER para sair...")
