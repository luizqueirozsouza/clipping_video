import json
import os
from pathlib import Path
from minio_storage import MinioStorage, MinioConfig

# Carrega configurações do .env
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "youtube-factory")

def get_storage():
    config = MinioConfig(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS,
        secret_key=MINIO_SECRET,
        secure=False # Mude para True se usar HTTPS
    )
    return MinioStorage(config)

def upload_cut_to_minio(video_path, metadata, channel_type, video_type="shorts"):
    """
    Sobe o vídeo e um arquivo metadata.json para o MinIO
    na estrutura: nicho/tipo/slug/
    """
    storage = get_storage()
    video_path = Path(video_path)
    slug = metadata.get("slug", video_path.stem)
    
    # Define o caminho no MinIO (Prefixo)
    # Ex: christian/shorts/pecado-e-juizo/
    remote_folder = f"{channel_type}/{video_type}/{slug}"
    
    # 1. Upload do Vídeo
    video_object = f"{remote_folder}/video.mp4"
    storage.upload_file(
        bucket=MINIO_BUCKET,
        file_path=video_path,
        object_name=video_object,
        content_type="video/mp4",
        create_bucket=True
    )
    print(f"✅ Vídeo enviado: {video_object}")

    # 2. Upload do JSON de Metadados
    # Salvamos um json temporário para subir
    json_path = video_path.parent / f"{slug}_meta.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    json_object = f"{remote_folder}/metadata.json"
    storage.upload_file(
        bucket=MINIO_BUCKET,
        file_path=json_path,
        object_name=json_object,
        content_type="application/json"
    )
    
    # Limpa json temporário
    os.remove(json_path)
    print(f"✅ Metadados enviados: {json_object}")
    
    return True