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
MINIO_ENDPOINT=dinastia-minio-s3.kb4x5f.easypanel.host
MINIO_ACCESS_KEY=sua_access_key
MINIO_SECRET_KEY=sua_secret_key
MINIO_SECURE=true
MINIO_BUCKET=youtube

# Performance & Processamento
PARALLEL_WORKERS=7        # Número de workers paralelos (ajuste conforme sua CPU)
PARALLEL_CHUNKS=4         # Chunks paralelos para renderização
SAMPLE_RATE=15            # Intervalo de frames para análise (10-30)
SAMPLE_FPS=1              # FPS para detecção de rostos
DETECTION_SCALE=640       # Escala de redimensionamento para detecção
USE_GPU=1                 # Usar aceleração por GPU se disponível (0/1)

# Estratégia de Conteúdo
PLATFORMS=shorts,youtube  # Opções: shorts, tiktok, youtube
CURRENT_PLATFORM=shorts   # Plataforma ativa para filtros específicos
AUTO_PREPROCESS=1         # Ativa pré-processamento automático

# Anti-Copyright & Descaracterização (DESCHAR)
DESCHAR_ENABLED=1         # Ativa filtros de descaracterização
REMOVE_LOGOS=1            # Aplica zoom para remover logos de borda
ZOOM_FACTOR=1.2           # Fator de zoom (1.2 = 20%)
IMAGES_DIR=images         # Pasta para sobreposição de imagens
IMAGE_FREQUENCY=30        # Frequência de imagens (em segundos)
IMAGE_DURATION=6          # Duração de cada imagem sobreposta

# Caminhos do Sistema
FFMPEG_PATH=ffmpeg        # Caminho para o executável do FFmpeg
VIDEO_LAYOUT=auto_podcast  # single ou auto_podcast
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

### 🐳 Alternativa: Execução via Docker (Recomendado para Produção)

Para uma instalação limpa e sem necessidade de configurar o FFmpeg manualmente no sistema host:

1.  **Certifique-se de ter o Docker e Docker Compose instalados.**
2.  **Suba o container:**
    ```bash
    docker-compose up --build
    ```
3.  Acesse a aplicação em: `http://localhost:8501`

## 📂 Estrutura de Pastas

- `/projeto_atual`: Pasta onde o vídeo mestre deve ser colocado ou enviado.
- `/cuts`: Pasta onde os cortes processados serão salvos.
- `/subs`: Arquivos temporários de legendagem.
- `/bkp`: Backup dos vídeos mestre após o processamento.

---

Desenvolvido para automação e escala de criação de conteúdo. 🚀
