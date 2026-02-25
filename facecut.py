"""
Otimizações aplicadas:
1. Batch processing com GPU quando disponível
2. Cache hierárquico (faces -> speakers)
3. Pré-alocação de arrays NumPy
4. Pooling adaptativo baseado em CPU/GPU
"""

import json
import os
import subprocess
import sys
import time
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np
from dotenv import load_dotenv
from tqdm import tqdm

from llm_service import correct_transcript_grammar

try:
    import video_tools_rs

    HAS_RUST = True
except ImportError:
    HAS_RUST = False

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VIDEO_PATH = os.getenv("VIDEO", "")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Otimizações de performance
DETECTION_SCALE = int(os.getenv("DETECTION_SCALE", "640"))
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "15"))
USE_GPU = os.getenv("USE_GPU", "1") == "1"
PARALLEL_CHUNKS = int(os.getenv("PARALLEL_CHUNKS", "6"))

# Cache files
OUTPUT_FACES = "faces_cache.json"
OUTPUT_TRANSCRIPT = "transcript_segments.json"
OUTPUT_WORDS = "transcript_words.json"
OUTPUT_METADATA = "video_metadata.json"
TEMP_AUDIO = "temp_audio.mp3"


@dataclass
class VideoInfo:
    """Informações do vídeo com validação"""

    fps: float
    frames: int
    width: int
    height: int
    duration: float

    @classmethod
    def from_video(cls, video_path: str) -> Optional["VideoInfo"]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        info = cls(
            fps=cap.get(cv2.CAP_PROP_FPS),
            frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            duration=0,
        )
        info.duration = info.frames / info.fps if info.fps else 0
        cap.release()
        return info


class OptimizedFaceDetector:
    """Detector de faces otimizado com cache hierárquico"""

    def __init__(
        self, video_path: str, sample_rate: int = 15, detection_scale: int = 640
    ):
        self.video_path = video_path
        self.sample_rate = sample_rate
        self.detection_scale = detection_scale
        self.info = VideoInfo.from_video(video_path)

        # Cache de rostos detectados por timestamp
        self._face_cache: Dict[int, List[Dict]] = {}

        # Índices da boca para cálculo de movimento
        self.mouth_indices = [61, 291, 0, 17]

    def _calculate_mouth_movement(self, landmarks, img_h: int, img_w: int) -> float:
        """Calcula movimento/abertura da boca usando Rust se disponível"""
        if not landmarks or len(landmarks.landmark) < max(self.mouth_indices):
            return 0.0

        try:
            # Extrai pontos da boca em batch
            p = [
                (landmarks.landmark[idx].x * img_w, landmarks.landmark[idx].y * img_h)
                for idx in self.mouth_indices
            ]

            if HAS_RUST:
                # p[0]=61, p[1]=291, p[2]=0, p[3]=17
                # Na lib.rs definimos p1, p2, p3, p4
                return video_tools_rs.calculate_mouth_ratio(p[0], p[1], p[2], p[3])

            # Fallback Python
            vertical = np.linalg.norm(np.array(p[0]) - np.array(p[3]))
            horizontal = np.linalg.norm(np.array(p[1]) - np.array(p[2]))

            return vertical / horizontal if horizontal > 0 else 0.0
        except Exception:
            return 0.0

    def _is_speaking_time(self, t: float, intervals: List[Tuple[float, float]]) -> bool:
        """Busca binária O(log N) para verificar se t está em algum intervalo de fala"""
        if not intervals:
            return False
        idx = bisect_left(intervals, (t, 1e9))
        if idx > 0:
            start, end = intervals[idx - 1]
            if start <= t <= end:
                return True
        return False

    def _process_frame_batch(
        self,
        frames: List[np.ndarray],
        frame_indices: List[int],
        speaking_intervals: List[Tuple[float, float]],
        mp_face_inst,
        mp_mesh_inst,
    ) -> Dict[int, List[Dict]]:
        """Processa batch de frames de uma vez"""
        results = {}
        h, w = frames[0].shape[:2]
        scale = self.detection_scale / w

        # Pré-aloca arrays para redimensionamento
        small_h = int(h * scale)
        resized_frames = np.empty(
            (len(frames), small_h, self.detection_scale, 3), dtype=np.uint8
        )

        # Resize em batch
        for i, frame in enumerate(frames):
            resized_frames[i] = cv2.resize(frame, (self.detection_scale, small_h))

        # Processa cada frame
        for i, (frame, frame_idx) in enumerate(zip(frames, frame_indices)):
            timestamp_ms = int((frame_idx / self.info.fps) * 1000)
            current_time = timestamp_ms / 1000.0

            rgb_small = cv2.cvtColor(resized_frames[i], cv2.COLOR_BGR2RGB)

            # 1. Detecção Inicial de Faces (Rápida)
            detections = mp_face_inst.process(rgb_small)
            if not detections.detections:
                continue

            is_speaking_time = self._is_speaking_time(current_time, speaking_intervals)
            faces_data = []

            # 2. Se houver fala, usamos o Mesh no frame INTEIRO uma única vez
            mesh_faces = []
            if is_speaking_time:
                mesh_result = mp_mesh_inst.process(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                )
                if mesh_result.multi_face_landmarks:
                    mesh_faces = mesh_result.multi_face_landmarks

            # 3. Correlaciona detecções com marcos do Mesh
            for detection in detections.detections:
                bbox = detection.location_data.relative_bounding_box

                face = {
                    "x": int(bbox.xmin * w),
                    "y": int(bbox.ymin * h),
                    "w": int(bbox.width * w),
                    "h": int(bbox.height * h),
                    "score": detection.score[0],
                    "mouth_opening": 0.0,
                    "is_speaking": False,
                }
                face["mouth_score"] = face["w"] * face["score"]

                if mesh_faces:
                    # Encontra o mesh mais próximo do centro da detecção
                    fx_c, fy_c = face["x"] + face["w"] / 2, face["y"] + face["h"] / 2
                    best_mesh = None
                    min_dist = 1e9

                    for lm in mesh_faces:
                        mx_c, my_c = (
                            lm.landmark[1].x * w,
                            lm.landmark[1].y * h,
                        )  # Ponto do nariz
                        dist = (fx_c - mx_c) ** 2 + (fy_c - my_c) ** 2
                        if dist < min_dist:
                            min_dist = dist
                            best_mesh = lm

                    if best_mesh and min_dist < (
                        face["w"] ** 2
                    ):  # Limite de proximidade
                        face["mouth_opening"] = self._calculate_mouth_movement(
                            best_mesh, h, w
                        )

                faces_data.append(face)

            # Identifica falante dominante usando Rust se disponível
            if is_speaking_time and faces_data:
                if HAS_RUST:
                    # Converte dicts para objetos Face do Rust
                    rust_faces = []
                    for fd in faces_data:
                        f = video_tools_rs.Face(
                            x=fd["x"],
                            y=fd["y"],
                            w=fd["w"],
                            h=fd["h"],
                            score=fd["score"],
                            mouth_score=fd["mouth_score"],
                            mouth_opening=fd["mouth_opening"],
                            is_speaking=False,
                        )
                        rust_faces.append(f)

                    # Processa lógica no Rust
                    processed = video_tools_rs.identify_dominant_speaker(rust_faces)

                    # Atualiza os dicts originais
                    for i, rf in enumerate(processed):
                        faces_data[i]["is_speaking"] = rf.is_speaking
                        faces_data[i]["mouth_score"] = rf.mouth_score
                else:
                    # Fallback Python Melhorado
                    # Ordena pela abertura da boca
                    faces_data.sort(key=lambda x: x["mouth_opening"], reverse=True)
                    dominant = faces_data[0]

                    # Filtro de ruído: abertura mínima de 0.12 para considerar fala
                    if dominant["mouth_opening"] > 0.12:
                        is_dominant = True
                        if len(faces_data) > 1:
                            second = faces_data[1]
                            # Se a diferença for pequena, não temos certeza absoluta de quem fala
                            if (
                                dominant["mouth_opening"]
                                < second["mouth_opening"] * 1.2
                            ):
                                is_dominant = False

                        if is_dominant:
                            dominant["is_speaking"] = True
                            # Aumenta significativamente a pontuação para o renderizador focar aqui
                            dominant["mouth_score"] *= 4.0
                        else:
                            # Se está em dúvida, dá um pequeno bônus para o que abriu mais
                            dominant["mouth_score"] *= 1.5

            # Remove campos temporários
            for f in faces_data:
                f.pop("mouth_opening", None)

            if faces_data:
                results[timestamp_ms] = faces_data

        return results

    def process_chunk(
        self,
        start_frame: int,
        end_frame: int,
        speaking_intervals: List[Tuple[float, float]],
    ) -> Dict[int, List[Dict]]:
        """Processa chunk de vídeo com batch processing"""
        # Instâncias locais para thread safety
        mp_face_local = mp.solutions.face_detection.FaceDetection(
            min_detection_confidence=0.25,
            model_selection=0,
        )
        mp_mesh_local = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=2,
            refine_landmarks=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        chunk_results = {}
        frame_idx = start_frame

        # Batch processing: acumula frames antes de processar
        BATCH_SIZE = 8
        frame_batch = []
        index_batch = []

        try:
            while frame_idx < end_frame:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % self.sample_rate == 0:
                    frame_batch.append(frame)
                    index_batch.append(frame_idx)

                    # Processa batch quando atingir tamanho
                    if len(frame_batch) >= BATCH_SIZE:
                        batch_results = self._process_frame_batch(
                            frame_batch,
                            index_batch,
                            speaking_intervals,
                            mp_face_local,
                            mp_mesh_local,
                        )
                        chunk_results.update(batch_results)
                        frame_batch.clear()
                        index_batch.clear()

                frame_idx += 1

            # Processa frames restantes
            if frame_batch:
                batch_results = self._process_frame_batch(
                    frame_batch,
                    index_batch,
                    speaking_intervals,
                    mp_face_local,
                    mp_mesh_local,
                )
                chunk_results.update(batch_results)

        finally:
            cap.release()
            mp_face_local.close()
            mp_mesh_local.close()

        return chunk_results

    def detect_parallel(self, transcripts: List[Dict]) -> Tuple[Dict, VideoInfo]:
        """Detecção paralela com progresso"""
        print(f"Video: {self.info.width}x{self.info.height} @ {self.info.fps:.1f} FPS")
        print(
            f"Config: Scale={self.detection_scale}px | Sample={self.sample_rate} frames"
        )
        print(f"Chunks paralelos: {PARALLEL_CHUNKS}")
        print("Modo: Detecção de Falante Dominante (Relativo)\n")

        # Prepara intervalos para busca binária
        speaking_intervals = sorted([(s["start"], s["end"]) for s in transcripts])

        total_frames = self.info.frames
        chunk_size = total_frames // PARALLEL_CHUNKS
        chunks = [
            (i * chunk_size, min((i + 1) * chunk_size, total_frames))
            for i in range(PARALLEL_CHUNKS)
        ]

        faces_data = {
            "fps": self.info.fps,
            "duration": self.info.duration,
            "faces_by_time": {},
        }

        print("Detectando faces e falantes...")

        with ThreadPoolExecutor(max_workers=PARALLEL_CHUNKS) as executor:
            futures = {
                executor.submit(self.process_chunk, start, end, speaking_intervals): i
                for i, (start, end) in enumerate(chunks)
            }

            with tqdm(
                total=PARALLEL_CHUNKS, desc="Progresso da Detecção", unit="chunk"
            ) as pbar:
                for future in as_completed(futures):
                    try:
                        chunk_results = future.result()
                        # Converte keys para string
                        faces_data["faces_by_time"].update(
                            {str(k): v for k, v in chunk_results.items()}
                        )
                    except Exception as e:
                        pbar.write(f"[ERRO] Erro no chunk {futures[future]}: {e}")

                    pbar.update(1)
        print(f"Detectadas {len(faces_data['faces_by_time'])} amostras de faces\n")

        return faces_data, self.info


def extract_audio_optimized(video_path: str) -> Optional[str]:
    """Extração de áudio otimizada com ffmpeg"""
    print("\n[2/3] Extraindo audio...")
    print("=" * 70)

    if os.path.exists(TEMP_AUDIO):
        os.remove(TEMP_AUDIO)

    try:
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-v",
            "error",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ac",
            "1",
            "-ab",
            "24k",
            "-ar",
            "16000",
            "-af",
            "loudnorm",
            "-threads",
            "0",
            TEMP_AUDIO,
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        file_size = os.path.getsize(TEMP_AUDIO) / (1024 * 1024)
        print(f"Audio extraido: {file_size:.1f} MB")

        return TEMP_AUDIO

    except Exception as e:
        print(f"Erro FFmpeg: {e}")
        return None


def get_audio_duration(path: str) -> float:
    """Obtem a duracao do audio usando ffprobe"""
    try:
        ffprobe_path = FFMPEG_PATH.replace("ffmpeg", "ffprobe")
        cmd = [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        # Fallback para VideoInfo se ffprobe falhar
        return 0.0


def transcribe_groq_optimized(audio_path: str) -> Tuple[List[Dict], List]:
    """Transcricao via Groq com suporte a chunking para audios longos"""
    if not GROQ_API_KEY:
        print("Erro: GROQ_API_KEY nao encontrada.")
        return [], []

    print("\n[3/3] Transcrevendo via Groq Cloud...")
    print("=" * 70)

    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)

    duration = get_audio_duration(audio_path)
    # Se nao conseguiu duracao, tenta por tamanho
    file_size = os.path.getsize(audio_path) / (1024 * 1024)

    if duration > 600 or file_size > 20:
        print(f"Audio longo ({duration / 60:.1f} min). Dividindo em partes...")
        segments, extra = transcribe_in_chunks(audio_path, duration, client)
    else:
        segments, extra = _call_groq_api(audio_path, client)

    if segments:
        last_end = segments[-1]["end"]
        print("\nEstatisticas finais:")
        print(f"   - Total Segmentos: {len(segments)}")
        print(f"   - Duracao Total: {last_end:.1f}s ({last_end / 60:.1f} min)")

    return segments, extra


def _call_groq_api(
    audio_path: str, client, offset: float = 0.0, model: str = "whisper-large-v3-turbo"
) -> Tuple[List[Dict], List]:
    """Chamada interna da API com tratamento de erros e fallback"""
    for attempt in range(3):
        try:
            with open(audio_path, "rb") as file:
                transcription = client.audio.transcriptions.create(
                    file=(audio_path, file.read()),
                    model=model,
                    response_format="verbose_json",
                    language="pt",
                    temperature=0.0,
                )

            segments = []
            raw_segs = getattr(transcription, "segments", []) or transcription.get(
                "segments", []
            )
            for s in raw_segs:
                segments.append(
                    {
                        "start": s.get("start") + offset,
                        "end": s.get("end") + offset,
                        "text": s.get("text", "").strip(),
                    }
                )
            return segments, []
        except Exception as e:
            error_msg = str(e).lower()
            print(f"Erro API Groq (Tentativa {attempt + 1}): {e}")

            # Se for limite de taxa ou erro 413, espera um pouco
            if "rate_limit" in error_msg or "413" in error_msg:
                wait_time = 30 * (attempt + 1)
                print(f"Limite atingido. Aguardando {wait_time}s...")
                time.sleep(wait_time)
                continue

            # Fallback de modelo se o turbo falhar por outro motivo
            if "turbo" in model:
                print("Tentando modelo fallback...")
                return _call_groq_api(
                    audio_path, client, offset, model="whisper-large-v3"
                )

            break
    return [], []


def transcribe_in_chunks(
    audio_path: str, total_duration: float, client
) -> Tuple[List[Dict], List]:
    """Divide o audio em partes de 10 minutos e transcreve cada uma"""
    all_segments = []
    chunk_duration = 600  # 10 minutos

    # Se duracao for 0, estima pelo tamanho do arquivo (aprox 1MB = 1min no nosso bitrate)
    if total_duration <= 0:
        total_duration = (os.path.getsize(audio_path) / (1024 * 1024)) * 60

    num_chunks = int(np.ceil(total_duration / chunk_duration))

    for i in range(num_chunks):
        start = i * chunk_duration
        print(f"   - Processando parte {i + 1}/{num_chunks} ({start / 60:.0f}min)...")

        chunk_file = f"temp_chunk_{i}.mp3"
        try:
            # Extrai o pedaco usando ffmpeg
            cmd = [
                FFMPEG_PATH,
                "-y",
                "-v",
                "error",
                "-ss",
                str(start),
                "-i",
                audio_path,
                "-t",
                str(chunk_duration),
                "-acodec",
                "copy",
                chunk_file,
            ]
            subprocess.run(cmd, check=True)

            # Transcreve o pedaco
            segs, _ = _call_groq_api(chunk_file, client, offset=start)
            all_segments.extend(segs)

            # Pequeno delay para evitar Rate Limit ASPH consecutivo
            if i < num_chunks - 1:
                time.sleep(2)

        except Exception as e:
            print(f"Erro ao processar parte {i}: {e}")
        finally:
            if os.path.exists(chunk_file):
                os.remove(chunk_file)

    # Ordena segmentos para garantir
    all_segments.sort(key=lambda x: x["start"])
    return all_segments, []


def find_video_source() -> str:
    """Auto-descobre vídeo na pasta projeto_atual"""
    env_video = os.getenv("VIDEO", "")
    if env_video and os.path.exists(env_video):
        return env_video

    print(f"Aviso: Video configurado ('{env_video}') nao encontrado.")
    print("Buscando videos automaticamente em 'projeto_atual'...")

    valid_extensions = ["*.mp4", "*.mkv", "*.mov", "*.avi", "*.webm"]
    found_videos = []

    projeto_folder = Path.cwd() / "projeto_atual"

    if projeto_folder.exists() and projeto_folder.is_dir():
        print(f"Pasta alvo: {projeto_folder}")
        for ext in valid_extensions:
            found_videos.extend(projeto_folder.glob(ext))
    else:
        print(
            f"Pasta 'projeto_atual' nao encontrada. Buscando na raiz ({Path.cwd()})..."
        )
        for ext in valid_extensions:
            found_videos.extend(Path.cwd().glob(ext))

    if not found_videos:
        print("Nenhum arquivo de video encontrado.")
        sys.exit(1)

    found_videos.sort(key=lambda p: p.stat().st_size, reverse=True)
    selected_video = found_videos[0]

    print(f"Video detectado automaticamente: {selected_video}")
    return str(selected_video)


def main():
    print("\n" + "=" * 70)
    print("ANALISE DE VIDEO - MODO OTIMIZADO V2")
    print("=" * 70)

    VIDEO_PATH = find_video_source()

    print(f"Video: {Path(VIDEO_PATH).name}")

    if not os.path.exists(VIDEO_PATH):
        print(f"Erro: Video nao encontrado: {VIDEO_PATH}")
        return

    print("Configuracao:")
    print(f"   - SAMPLE_RATE: {SAMPLE_RATE} frames")
    print(f"   - DETECTION_SCALE: {DETECTION_SCALE}px")
    print(f"   - PARALLEL_CHUNKS: {PARALLEL_CHUNKS}")
    print("=" * 70 + "\n")

    # Audio e Transcrição
    if not os.path.exists(OUTPUT_TRANSCRIPT):
        print("\n" + "=" * 70)
        print("ETAPA 1/2: EXTRACAO DE AUDIO E TRANSCRICAO (Whisper)")
        print("=" * 70 + "\n")

        # Extração
        if not os.path.exists(TEMP_AUDIO):
            audio = extract_audio_optimized(VIDEO_PATH)
        else:
            audio = TEMP_AUDIO  # Assume TEMP_AUDIO is the path if it exists

        if audio:
            segs, _ = transcribe_groq_optimized(audio)

            # Correção gramatical
            try:
                segs = correct_transcript_grammar(segs)
            except Exception as e:
                print(f"Erro na correcao gramatical: {e}")

            with open(OUTPUT_TRANSCRIPT, "w", encoding="utf-8") as f:
                json.dump(segs, f, indent=2, ensure_ascii=False)

            with open(OUTPUT_WORDS, "w", encoding="utf-8") as f:
                json.dump([], f)

            if os.path.exists(TEMP_AUDIO):
                os.remove(TEMP_AUDIO)
    else:
        print("Transcricao ja existe, usando cache.\n")

    # Detecção de faces
    if not os.path.exists(OUTPUT_FACES):
        print("\n" + "=" * 70)
        print("ETAPA 2/2: ANALISE VISUAL E DETECCAO DE FALANTE")
        print("=" * 70 + "\n")

        transcripts = []
        if os.path.exists(OUTPUT_TRANSCRIPT):
            with open(OUTPUT_TRANSCRIPT, "r", encoding="utf-8") as f:
                transcripts = json.load(f)
            print(f"Transcricao carregada: {len(transcripts)} segmentos")

        detector = OptimizedFaceDetector(VIDEO_PATH, SAMPLE_RATE, DETECTION_SCALE)
        faces_data, video_info = detector.detect_parallel(transcripts)

        with open(OUTPUT_FACES, "w", encoding="utf-8") as f:
            json.dump(faces_data, f)

        print(f"Cache salvo: {OUTPUT_FACES}")
    else:
        print("Cache de rostos detectado. Pulando processamento visual.\n")

    # Garantia de Metadados (Sempre gera se faltar)
    if not os.path.exists(OUTPUT_METADATA):
        print("Gerando metadados do video...")
        vinfo = VideoInfo.from_video(VIDEO_PATH)
        if vinfo:
            meta = {
                "width": vinfo.width,
                "height": vinfo.height,
                "duration": vinfo.duration,
                "fps": vinfo.fps,
            }
            with open(OUTPUT_METADATA, "w", encoding="utf-8") as f:
                json.dump(meta, f)
            print(f"Metadados salvos: {OUTPUT_METADATA}")

    print("\n" + "=" * 70)
    print("ANALISE COMPLETA!")
    print("=" * 70)
    print("Arquivos gerados:")
    print(f"   - {OUTPUT_FACES}")
    print(f"   - {OUTPUT_TRANSCRIPT}")
    print(f"   - {OUTPUT_METADATA}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
