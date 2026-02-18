from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union
from urllib.parse import urlparse

from minio import Minio

PathLike = Union[str, Path]


@dataclass(frozen=True)
class MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool = False
    region: Optional[str] = None


class MinioStorage:
    """
    Wrapper simples para operações comuns no MinIO:
    - criar bucket se não existir
    - upload (arquivo local -> objeto)
    - download (objeto -> arquivo local)
    - listar/consultar
    - stat (metadados)
    - apagar
    - gerar URL temporária (presigned GET)
    """

    def __init__(self, config: MinioConfig):
        self.config = config
        self.client = Minio(
            endpoint=config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=config.secure,
            region=config.region,
        )

    @staticmethod
    def from_env(prefix: str = "MINIO_") -> "MinioStorage":
        raw_endpoint = os.getenv(f"{prefix}ENDPOINT", "localhost:9000").strip()

        # Aceita endpoint com http(s):// e remove scheme/path
        if raw_endpoint.startswith(("http://", "https://")):
            u = urlparse(raw_endpoint)
            endpoint = u.netloc  # host[:port]
            # Se MINIO_SECURE não estiver setado, inferir pelo scheme
            secure = u.scheme == "https"
        else:
            endpoint = raw_endpoint.strip().rstrip("/")
            secure = os.getenv(f"{prefix}SECURE", "false").lower() in (
                "1",
                "true",
                "yes",
            )

        access_key = os.getenv(f"{prefix}ACCESS_KEY", "minioadmin")
        secret_key = os.getenv(f"{prefix}SECRET_KEY", "minioadmin")

        cfg = MinioConfig(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        return MinioStorage(cfg)

    # -----------------------------
    # Buckets
    # -----------------------------
    def bucket_exists(self, bucket: str) -> bool:
        return self.client.bucket_exists(bucket)

    def ensure_bucket(self, bucket: str) -> None:
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    # -----------------------------
    # Upload / Download
    # -----------------------------
    def upload_file(
        self,
        bucket: str,
        file_path: PathLike,
        object_name: Optional[str] = None,
        content_type: str = "application/octet-stream",
        create_bucket: bool = True,
    ) -> dict:
        """
        Sobe um arquivo local para o MinIO.
        Retorna infos úteis (etag, version_id, bucket, object_name).
        """
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {p}")

        if create_bucket:
            self.ensure_bucket(bucket)

        obj = object_name or p.name
        res = self.client.fput_object(
            bucket_name=bucket,
            object_name=obj,
            file_path=str(p),
            content_type=content_type,
        )
        return {
            "bucket": bucket,
            "object_name": obj,
            "etag": res.etag,
            "version_id": res.version_id,
        }

    def download_file(
        self,
        bucket: str,
        object_name: str,
        out_path: PathLike,
        make_dirs: bool = True,
    ) -> Path:
        """
        Baixa um objeto para um caminho local.
        Retorna o Path final.
        """
        out = Path(out_path)
        if make_dirs:
            out.parent.mkdir(parents=True, exist_ok=True)

        self.client.fget_object(bucket, object_name, str(out))
        return out

    # -----------------------------
    # List / Stat
    # -----------------------------
    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        recursive: bool = True,
    ) -> List[Tuple[str, int, Optional[str]]]:
        """
        Lista objetos e devolve uma lista de tuplas:
        (object_name, size_bytes, last_modified_iso)
        """
        if not self.client.bucket_exists(bucket):
            return []

        results: List[Tuple[str, int, Optional[str]]] = []
        for obj in self.client.list_objects(bucket, prefix=prefix, recursive=recursive):
            last_mod = obj.last_modified.isoformat() if obj.last_modified else None
            size = obj.size if obj.size is not None else 0
            results.append((obj.object_name, size, last_mod))
        return results

    def stat_object(self, bucket: str, object_name: str) -> dict:
        """
        Consulta metadados do objeto.
        """
        st = self.client.stat_object(bucket, object_name)
        return {
            "bucket": bucket,
            "object_name": object_name,
            "size": st.size,
            "etag": st.etag,
            "content_type": st.content_type,
            "last_modified": st.last_modified.isoformat() if st.last_modified else None,
            "metadata": dict(st.metadata) if st.metadata else {},
            "version_id": getattr(st, "version_id", None),
        }

    # -----------------------------
    # Delete
    # -----------------------------
    def remove_object(self, bucket: str, object_name: str) -> None:
        self.client.remove_object(bucket, object_name)

    def remove_objects(self, bucket: str, object_names: Iterable[str]) -> List[str]:
        """
        Remove vários objetos.
        Retorna lista de erros (strings) se houver.
        """
        errs = self.client.remove_objects(bucket, object_names)
        errors: List[str] = []
        for e in errs:
            errors.append(f"{e.object_name}: {e.message}")
        return errors

    # -----------------------------
    # Presigned URL
    # -----------------------------
    def presigned_get_url(
        self,
        bucket: str,
        object_name: str,
        expiry_seconds: int = 3600,
    ) -> str:
        return self.client.presigned_get_object(
            bucket_name=bucket,
            object_name=object_name,
            expires=timedelta(seconds=expiry_seconds),
        )

    # -----------------------------
    # Helpers (opcional)
    # -----------------------------
    @staticmethod
    def guess_content_type(file_path: PathLike) -> str:
        """
        Heurística simples por extensão (pode trocar por mimetypes).
        """
        p = Path(file_path)
        ext = p.suffix.lower()
        if ext == ".mp4":
            return "video/mp4"
        if ext == ".webm":
            return "video/webm"
        if ext in (".mov", ".mkv", ".avi"):
            return "video/*"
        if ext == ".json":
            return "application/json"
        if ext in (".jpg", ".jpeg"):
            return "image/jpeg"
        if ext == ".png":
            return "image/png"
        return "application/octet-stream"
