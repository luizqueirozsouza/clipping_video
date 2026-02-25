import json
import os
import time

from facecut import HAS_RUST, OptimizedFaceDetector


def test_detection_small_segment():
    video_path = "projeto_atual/video_master_1771775088.mp4"
    if not os.path.exists(video_path):
        print("Vídeo não encontrado.")
        return

    print(f"Iniciando teste de detecção (Rust Enabled: {HAS_RUST})")

    # Carregar apenas alguns segundos de transcrição se existir
    transcripts = []
    if os.path.exists("transcript_segments.json"):
        with open("transcript_segments.json", "r", encoding="utf-8") as f:
            full_transcripts = json.load(f)
            # Pegar apenas os primeiros 30 segundos
            transcripts = [t for t in full_transcripts if t["end"] <= 30]

    detector = OptimizedFaceDetector(video_path, sample_rate=15, detection_scale=640)

    start_time = time.time()
    # Processar apenas de 0 a 1000 frames (aprox 30-40s dependendo do FPS)
    print("Processando primeiros 1000 frames para teste...")
    results = detector.process_chunk(
        0, 1000, [(s["start"], s["end"]) for s in transcripts]
    )
    end_time = time.time()

    duration = end_time - start_time
    print(f"\nTeste concluído em {duration:.2f} segundos.")
    print(f"Frames analisados: {len(results)} amostras")

    speaking_count = sum(
        1 for faces in results.values() for f in faces if f.get("is_speaking")
    )
    print(f"Faces marcadas como falantes: {speaking_count}")

    if HAS_RUST:
        print("\n✅ Verificado: O processamento utilizou as rotas otimizadas em Rust.")
    else:
        print("\n⚠️ Aviso: O processamento utilizou o fallback em Python.")


if __name__ == "__main__":
    test_detection_small_segment()
