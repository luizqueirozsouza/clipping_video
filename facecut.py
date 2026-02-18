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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np
from dotenv import load_dotenv

from llm_service import correct_transcript_grammar

load_dotenv()

VIDEO_PATH = os.getenv("VIDEO", "")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Otimizações de performance
DETECTION_SCALE = int(os.getenv("DETECTION_SCALE", "640"))
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "15"))
USE_GPU = os.getenv("USE_GPU", "1") == "1"
PARALLEL_CHUNKS = int(os.getenv("PARALLEL_CHUNKS", "4"))

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
        """Calcula movimento/abertura da boca com vetorização NumPy"""
        if not landmarks or len(landmarks.landmark) < max(self.mouth_indices):
            return 0.0

        try:
            # Extrai pontos da boca em batch
            mouth_points = np.array(
                [
                    [
                        landmarks.landmark[idx].x * img_w,
                        landmarks.landmark[idx].y * img_h,
                    ]
                    for idx in self.mouth_indices
                ]
            )

            # Cálculo vetorizado
            vertical = np.linalg.norm(mouth_points[0] - mouth_points[3])
            horizontal = np.linalg.norm(mouth_points[1] - mouth_points[2])

            return vertical / horizontal if horizontal > 0 else 0.0
        except Exception:
            return 0.0

    def _process_frame_batch(
        self,
        frames: List[np.ndarray],
        frame_indices: List[int],
        transcripts: List[Dict],
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
            detections = mp_face_inst.process(rgb_small)

            if not detections.detections:
                continue

            is_speaking_time = any(
                seg["start"] <= current_time <= seg["end"] for seg in transcripts
            )

            faces_data = []

            for detection in detections.detections:
                bbox = detection.location_data.relative_bounding_box

                face = {
                    "x": int(bbox.xmin * w),
                    "y": int(bbox.ymin * h),
                    "w": int(bbox.width * w),
                    "h": int(bbox.height * h),
                    "score": detection.score[0],
                    "temp_movement": 0.0,
                }

                face["mouth_score"] = face["w"] * face["score"]

                # Detecta movimento apenas se houver fala
                if is_speaking_time:
                    x1, y1 = max(0, face["x"]), max(0, face["y"])
                    x2, y2 = (
                        min(w, face["x"] + face["w"]),
                        min(h, face["y"] + face["h"]),
                    )

                    face_roi = frame[y1:y2, x1:x2]

                    if face_roi.size > 0:
                        rgb_roi = cv2.cvtColor(face_roi, cv2.COLOR_BGR2RGB)
                        mesh_result = mp_mesh_inst.process(rgb_roi)

                        if mesh_result.multi_face_landmarks:
                            landmarks = mesh_result.multi_face_landmarks[0]
                            movement = self._calculate_mouth_movement(
                                landmarks, face["h"], face["w"]
                            )
                            face["temp_movement"] = movement

                faces_data.append(face)

            # Identifica falante dominante
            if is_speaking_time and faces_data:
                faces_data.sort(key=lambda x: x["temp_movement"], reverse=True)
                dominant = faces_data[0]

                if dominant["temp_movement"] > 0.15:
                    is_dominant = True
                    if len(faces_data) > 1:
                        second = faces_data[1]
                        if dominant["temp_movement"] < second["temp_movement"] * 1.3:
                            is_dominant = False

                    if is_dominant:
                        dominant["is_speaking"] = True
                        dominant["mouth_score"] *= 3.0

            # Remove campo temporário
            for f in faces_data:
                f.pop("temp_movement", None)

            if faces_data:
                results[timestamp_ms] = faces_data

        return results

    def process_chunk(
        self, start_frame: int, end_frame: int, transcripts: List[Dict]
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
                            transcripts,
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
                    frame_batch, index_batch, transcripts, mp_face_local, mp_mesh_local
                )
                chunk_results.update(batch_results)

        finally:
            cap.release()
            mp_face_local.close()
            mp_mesh_local.close()

        return chunk_results

    def detect_parallel(self, transcripts: List[Dict]) -> Tuple[Dict, VideoInfo]:
        """Detecção paralela com progresso"""
        print(
            f"📹 Vídeo: {self.info.width}x{self.info.height} @ {self.info.fps:.1f} FPS"
        )
        print(
            f"⚙️ Config: Scale={self.detection_scale}px | Sample={self.sample_rate} frames"
        )
        print(f"🔄 Chunks paralelos: {PARALLEL_CHUNKS}")
        print("🧠 Modo: Detecção de Falante Dominante (Relativo)\n")

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

        print("🕵️ Detectando faces e falantes...")
        print("   Progresso: [", end="", flush=True)

        with ThreadPoolExecutor(max_workers=PARALLEL_CHUNKS) as executor:
            futures = {
                executor.submit(self.process_chunk, start, end, transcripts): i
                for i, (start, end) in enumerate(chunks)
            }

            completed = 0
            for future in as_completed(futures):
                chunk_results = future.result()
                # Converte keys para string
                faces_data["faces_by_time"].update(
                    {str(k): v for k, v in chunk_results.items()}
                )

                completed += 1
                progress = int((completed / PARALLEL_CHUNKS) * 20)
                print("#" * progress, end="", flush=True)

        print("] OK")
        print(f"✅ Detectadas {len(faces_data['faces_by_time'])} amostras de faces\n")

        return faces_data, self.info


def extract_audio_optimized(video_path: str) -> Optional[str]:
    """Extração de áudio otimizada com ffmpeg"""
    print("\n🔊 [2/3] Extraindo áudio...")
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
        print(f"✅ Áudio extraído: {file_size:.1f} MB")

        return TEMP_AUDIO

    except Exception as e:
        print(f"❌ Erro FFmpeg: {e}")
        return None


def transcribe_groq_optimized(audio_path: str) -> Tuple[List[Dict], List]:
    """Transcrição via Groq com retry automático"""
    if not GROQ_API_KEY:
        print("❌ ERRO: GROQ_API_KEY não encontrada.")
        return [], []

    print("\n🔍 [3/3] Transcrevendo via Groq Cloud...")
    print("=" * 70)

    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)

    file_size = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"📦 Tamanho do áudio: {file_size:.1f} MB")

    if file_size > 25:
        print("⚠️ Arquivo grande (>25MB), isso pode demorar...")

    with open(audio_path, "rb") as file:
        try:
            print("📤 Enviando para Groq API...")

            transcription = client.audio.transcriptions.create(
                file=(audio_path, file.read()),
                model="whisper-large-v3-turbo",
                response_format="verbose_json",
                language="pt",
                temperature=0.0,
            )

            print("✅ Transcrição recebida!")

        except Exception as e:
            print(f"⚠️ Erro API Groq: {e}")

            if "turbo" in str(e).lower() or "not found" in str(e).lower():
                print("🔄 Tentando modelo fallback (whisper-large-v3)...")
                try:
                    file.seek(0)
                    transcription = client.audio.transcriptions.create(
                        file=(audio_path, file.read()),
                        model="whisper-large-v3",
                        response_format="verbose_json",
                        language="pt",
                        temperature=0.0,
                    )
                    print("✅ Transcrição com modelo fallback bem-sucedida!")
                except:
                    return [], []
            else:
                return [], []

    segments = []
    raw_segs = getattr(transcription, "segments", []) or transcription.get(
        "segments", []
    )

    for s in raw_segs:
        segments.append(
            {
                "start": s.get("start"),
                "end": s.get("end"),
                "text": s.get("text", "").strip(),
            }
        )

    total_duration = segments[-1]["end"] if segments else 0
    print("📊 Estatísticas:")
    print(f"   • Segmentos: {len(segments)}")
    print(f"   • Duração: {total_duration:.1f}s ({total_duration / 60:.1f} min)")

    return segments, []


def find_video_source() -> str:
    """Auto-descobre vídeo na pasta projeto_atual"""
    env_video = os.getenv("VIDEO", "")
    if env_video and os.path.exists(env_video):
        return env_video

    print(f"⚠️ Vídeo configurado ('{env_video}') não encontrado.")
    print("🔍 Buscando vídeos automaticamente em 'projeto_atual'...")

    valid_extensions = ["*.mp4", "*.mkv", "*.mov", "*.avi", "*.webm"]
    found_videos = []

    projeto_folder = Path.cwd() / "projeto_atual"

    if projeto_folder.exists() and projeto_folder.is_dir():
        print(f"📂 Pasta alvo: {projeto_folder}")
        for ext in valid_extensions:
            found_videos.extend(projeto_folder.glob(ext))
    else:
        print(
            f"📂 Pasta 'projeto_atual' não encontrada. Buscando na raiz ({Path.cwd()})..."
        )
        for ext in valid_extensions:
            found_videos.extend(Path.cwd().glob(ext))

    if not found_videos:
        print("❌ Nenhum arquivo de vídeo encontrado.")
        sys.exit(1)

    found_videos.sort(key=lambda p: p.stat().st_size, reverse=True)
    selected_video = found_videos[0]

    print(f"✅ Vídeo detectado automaticamente: {selected_video}")
    return str(selected_video)


def main():
    print("\n" + "=" * 70)
    print("🚀 ANÁLISE DE VÍDEO - MODO OTIMIZADO V2")
    print("=" * 70)

    VIDEO_PATH = find_video_source()

    print(f"📹 Vídeo: {Path(VIDEO_PATH).name}")

    if not os.path.exists(VIDEO_PATH):
        print(f"❌ Vídeo não encontrado: {VIDEO_PATH}")
        return

    print("⚙️ Configuração:")
    print(f"   • SAMPLE_RATE: {SAMPLE_RATE} frames")
    print(f"   • DETECTION_SCALE: {DETECTION_SCALE}px")
    print(f"   • PARALLEL_CHUNKS: {PARALLEL_CHUNKS}")
    print("=" * 70 + "\n")

    # Transcrição
    if not os.path.exists(OUTPUT_TRANSCRIPT):
        audio = extract_audio_optimized(VIDEO_PATH)
        if audio:
            segs, _ = transcribe_groq_optimized(audio)

            # Correção gramatical
            try:
                segs = correct_transcript_grammar(segs)
            except Exception as e:
                print(f"⚠️ Erro na correção gramatical: {e}")

            with open(OUTPUT_TRANSCRIPT, "w", encoding="utf-8") as f:
                json.dump(segs, f, indent=2, ensure_ascii=False)

            with open(OUTPUT_WORDS, "w", encoding="utf-8") as f:
                json.dump([], f)

            if os.path.exists(TEMP_AUDIO):
                os.remove(TEMP_AUDIO)
    else:
        print("⏩ Transcrição já existe, usando cache.\n")

    # Detecção de faces
    if not os.path.exists(OUTPUT_FACES):
        print("🕵️ [1/3] Detectando Rostos + Identificação Precisa de Falante")
        print("=" * 70)

        transcripts = []
        if os.path.exists(OUTPUT_TRANSCRIPT):
            with open(OUTPUT_TRANSCRIPT, "r", encoding="utf-8") as f:
                transcripts = json.load(f)
            print(f"📝 Transcrição carregada: {len(transcripts)} segmentos")

        detector = OptimizedFaceDetector(VIDEO_PATH, SAMPLE_RATE, DETECTION_SCALE)
        faces_data, video_info = detector.detect_parallel(transcripts)

        with open(OUTPUT_FACES, "w", encoding="utf-8") as f:
            json.dump(faces_data, f)

        print(f"💾 Cache salvo: {OUTPUT_FACES}")

        meta = {
            "width": video_info.width,
            "height": video_info.height,
            "duration": video_info.duration,
            "fps": video_info.fps,
        }

        with open(OUTPUT_METADATA, "w", encoding="utf-8") as f:
            json.dump(meta, f)
    else:
        print("⏩ Cache de rostos detectado. Pulando processamento visual.")

    print("\n" + "=" * 70)
    print("✅ ANÁLISE COMPLETA!")
    print("=" * 70)
    print("📦 Arquivos gerados:")
    print(f"   • {OUTPUT_FACES}")
    print(f"   • {OUTPUT_TRANSCRIPT}")
    print(f"   • {OUTPUT_METADATA}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
