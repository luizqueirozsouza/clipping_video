# Use uma imagem base do Python leve
FROM python:3.10-slim

# Instala dependências do sistema necessárias para o FFmpeg e processamento de vídeo
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1-mesa-glx \
    git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia o arquivo de dependências primeiro (para aproveitar o cache de camadas do Docker)
COPY requirements.txt .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante dos arquivos do projeto para o container
COPY . .

# Cria as pastas necessárias para o funcionamento do app
RUN mkdir -p projeto_atual cuts subs bkp tokens outputs

# Expõe a porta padrão do Streamlit
EXPOSE 8501

# Variável de ambiente para garantir que o Python não bufferize a saída do log
ENV PYTHONUNBUFFERED=1

# Comando para rodar a aplicação
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
