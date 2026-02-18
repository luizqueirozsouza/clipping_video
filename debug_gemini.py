# debug_gemini.py
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ Erro: GEMINI_API_KEY não encontrada no .env")
else:
    print(f"🔑 Testando chave: {api_key[:5]}...{api_key[-3:]}")
    try:
        client = genai.Client(api_key=api_key)
        print("📡 Conectando ao Google...")
        
        # Lista modelos disponíveis
        pager = client.models.list()
        
        print("\n✅ Modelos Disponíveis para sua chave:")
        found_flash = False
        for model in pager:
            if "gemini" in model.name:
                print(f"   - {model.name}")
            if "flash" in model.name:
                found_flash = True
        
        if not found_flash:
            print("\n⚠️ AVISO: Nenhum modelo 'flash' encontrado. Talvez precise ativar a API no Google AI Studio?")
            
    except Exception as e:
        print(f"\n❌ ERRO DE CONEXÃO: {e}")