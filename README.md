# 🎬 AI Video Slicer (Clipping Video)

Um pipeline automatizado para transformar vídeos longos em cortes otimizados para redes sociais (Shorts, TikTok, Reels), utilizando Inteligência Artificial para análise, corte e legendagem dinâmica.

## 🚀 Funcionalidades

- **Análise com IA:** Utiliza modelos avançados (Gemini, GPT, Groq) para identificar momentos virais e relevantes.
- **Detecção Facial:** Enquadramento inteligente baseado em reconhecimento facial.
- **Legendagem Dinâmica:** Geração automática de legendas estilo "Karaokê" com cores e fontes customizáveis.
- **Multi-Plataforma:** Suporte para cortes verticais (9:16) e horizontais (YouTube).
- **Anti-Copyright:** Filtros de espelhamento e ajustes de vídeo para evitar detecção automatizada de direitos autorais.
- **Integração com MinIO:** Upload automatizado para buckets S3-like, facilitando a integração com automações (n8n).
- **Gerenciamento de Backup:** Sistema de reset total com opção de backup local para garantir a segurança dos arquivos originais.

## 🛠️ Tecnologias Utilizadas

- **Core:** Python 3.10+
- **Interface:** Streamlit
- **Processamento de Vídeo:** FFmpeg, MoviePy
- **IA/LLM:** Google Gemini, OpenAI, Groq, OpenRouter
- **Storage:** MinIO (S3)
- **Infra:** Docker (opcional) / n8n para automação de postagem

## 📋 Pré-requisitos

1.  **FFmpeg** instalado e configurado no PATH do sistema.
2.  **MinIO** configurado e acessível.
3.  Contas/Chaves de API para os modelos de IA desejados.

## ⚙️ Configuração (Arquivo .env)

Crie um arquivo `.env` na raiz do projeto com as seguintes chaves:

```env
# AI API Keys
GROQ_API_KEY=sua_chave_aqui
GEMINI_API_KEY=sua_chave_aqui
OPENAI_API_KEY=sua_chave_aqui
OPENROUTER_API_KEY=sua_chave_aqui
OPENROUTER_MODEL=google/gemini-2.0-flash-001

# Storage (MinIO)
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_SECURE=False
MINIO_BUCKET=youtube

# Outros
FFMPEG_PATH=ffmpeg
```

## 🏃 Como Executar

1.  **Clone o repositório:**

    ```bash
    git clone https://github.com/luizqueirozsouza/clipping_video.git
    cd clipping_video
    ```

2.  **Instale as dependências:**
    (Recomendado utilizar ambientes virtuais como `uv` ou `venv`)

    ```bash
    pip install -r requirements.txt
    ```

3.  **Inicie a aplicação:**
    ```bash
    streamlit run app.py
    ```

## 📂 Estrutura de Pastas

- `/projeto_atual`: Pasta onde o vídeo mestre deve ser colocado ou enviado.
- `/cuts`: Pasta onde os cortes processados serão salvos.
- `/subs`: Arquivos temporários de legendagem.
- `/bkp`: Backup dos vídeos mestre após o processamento.
- `/tokens`: Arquivos de autenticação para serviços (Google/Drive).

---

Desenvolvido para automação e escala de criação de conteúdo. 🚀
