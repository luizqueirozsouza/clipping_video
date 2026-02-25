# Use uma imagem base do Python leve
FROM python:3.10-slim

# Evita que o Python gere arquivos .pyc e bufferize os logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instala dependências do sistema e ferramentas de build
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    git \
    build-essential \
    curl \
    pkg-config \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Instala o Rust (necessário para compilar extensões Rust como video_tools_rs)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Define o diretório de trabalho
WORKDIR /app

# Instala o 'uv' para gerenciamento rápido de pacotes
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copia os arquivos de definição de projeto e o README (necessário para o metadata do projeto)
COPY pyproject.toml uv.lock README.md ./

# Instala as dependências usando uv (compila e instala no sistema)
RUN uv pip install --system --no-cache .

# Copia o restante dos arquivos do projeto
COPY . .

# Cria os diretórios necessários
RUN mkdir -p projeto_atual cuts subs bkp tokens outputs images

# Expõe a porta do Streamlit
EXPOSE 8501

# Comando para rodar a aplicação
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
