"""
Script de pré-processamento que calcula todas as decisões de layout
ANTES da renderização. Isso permite:

1. Processar todos os cortes em paralelo sem conflitos
2. Validar decisões antes de começar a renderizar
3. Ajustar thresholds e re-calcular sem re-renderizar
"""

import json
import os
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path.cwd()
VIDEO_PATH = os.getenv("VIDEO", "")
CURRENT_PLATFORM = os.getenv("CURRENT_PLATFORM", "shorts")
FACECUT_JSON = Path(os.getenv("FACECUT_JSON", f"{CURRENT_PLATFORM}_cuts.json"))
FACES_CACHE = BASE_DIR / "faces_cache.json"
LAYOUT_CACHE = BASE_DIR / "layout_decisions_cache.json"


class LayoutPreprocessor:
    """
    Pré-calcula todas as decisões de layout com análise estatística
    """

    def __init__(self, faces_data, clip_w, clip_h):
        self.faces_data = faces_data
        self.clip_w = clip_w
        self.clip_h = clip_h
        self.decisions = {}
        self.stats = {"split_count": 0, "solo_count": 0, "speaking_detected": 0}

    def analyze_segment(self, start_time, end_time, sample_times=7):
        """
        Análise detalhada de um segmento com mais amostras
        """
        times = np.linspace(start_time, end_time, sample_times)

        split_votes = 0
        solo_votes = 0
        speaker_positions = []
        confidence_scores = []

        for t in times:
            key = str(int(t * 1000))
            faces = self.faces_data.get("faces_by_time", {}).get(key, [])

            if len(faces) < 2:
                solo_votes += 1
                confidence = 0.8 if len(faces) == 1 else 0.5
                confidence_scores.append(confidence)

                if len(faces) == 1:
                    speaker_positions.append(faces[0])
                continue

            faces = sorted(faces, key=lambda x: x["x"])

            # Detecta falante ativo
            speaking_faces = [f for f in faces if f.get("is_speaking", False)]

            if len(speaking_faces) == 1:
                solo_votes += 2
                confidence_scores.append(0.9)  # alta confiança em solo
                speaker_positions.append(speaking_faces[0])
                self.stats["speaking_detected"] += 1
                continue

            # Calcula separação entre faces
            leftmost = faces[0]
            rightmost = faces[-1]

            center_distance = (rightmost["x"] + rightmost["w"] / 2) - (
                leftmost["x"] + leftmost["w"] / 2
            )

            separation_ratio = center_distance / self.clip_w

            # Sistema de confiança graduado
            if separation_ratio > 0.15:
                split_votes += 1
                confidence = min(1.0, separation_ratio / 0.3)  # 0.3 = muito separado
                confidence_scores.append(confidence)
                speaker_positions.extend([leftmost, rightmost])
            elif separation_ratio > 0.12:
                split_votes += 0.5  # voto parcial
                confidence_scores.append(0.6)
                speaker_positions.extend([leftmost, rightmost])
            else:
                solo_votes += 1
                confidence_scores.append(0.7)

        # Decisão final
        is_split = split_votes > solo_votes
        avg_confidence = np.mean(confidence_scores) if confidence_scores else 0.5

        return {
            "is_split": is_split,
            "speaker_positions": speaker_positions,
            "confidence": avg_confidence,
            "split_votes": split_votes,
            "solo_votes": solo_votes,
            "duration": end_time - start_time,
        }

    def process_all_cuts(self, cuts):
        """
        Processa todos os cortes e gera relatório
        """
        print("🔍 Analisando todos os segmentos...\n")

        for i, cut in enumerate(cuts):
            start = float(cut["start"])
            end = float(cut["end"])

            decision = self.analyze_segment(start, end)

            cache_key = f"{start:.3f}_{end:.3f}"
            self.decisions[cache_key] = decision

            if decision["is_split"]:
                self.stats["split_count"] += 1
                mode_icon = "🔀"
                mode_text = "SPLIT"
            else:
                self.stats["solo_count"] += 1
                mode_icon = "🎯"
                mode_text = "SOLO "

            confidence_bar = "█" * int(decision["confidence"] * 10)
            print(
                f"{mode_icon} Cut {i:02} | {mode_text} | "
                f"Confiança: {confidence_bar} {decision['confidence']:.0%} | "
                f"Duração: {decision['duration']:.1f}s"
            )

        return self.decisions

    def save_cache(self):
        """Salva decisões em cache"""
        cache_data = {}
        for key, decision in self.decisions.items():
            # Simplifica para salvar no cache
            cache_data[key] = {
                "is_split": decision["is_split"],
                "speaker_positions": decision["speaker_positions"],
            }

        with open(LAYOUT_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)

        print(f"\n💾 Cache salvo: {LAYOUT_CACHE}")

    def print_summary(self):
        """Imprime resumo estatístico"""
        total = self.stats["split_count"] + self.stats["solo_count"]

        print("\n" + "=" * 60)
        print("📊 RESUMO DA ANÁLISE")
        print("=" * 60)
        print(f"Total de cortes: {total}")
        print(
            f"Modo SPLIT: {self.stats['split_count']} ({self.stats['split_count'] / total * 100:.1f}%)"
        )
        print(
            f"Modo SOLO:  {self.stats['solo_count']} ({self.stats['solo_count'] / total * 100:.1f}%)"
        )
        print(f"Falante ativo detectado: {self.stats['speaking_detected']} vezes")
        print("=" * 60)

    def generate_report(self, output_file="layout_report.txt"):
        """Gera relatório detalhado em arquivo"""
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("RELATÓRIO DE ANÁLISE DE LAYOUT\n")
            f.write("=" * 60 + "\n\n")

            for key, decision in self.decisions.items():
                start, end = key.split("_")
                mode = "SPLIT" if decision["is_split"] else "SOLO"

                f.write(f"Segmento: {start}s - {end}s\n")
                f.write(f"  Modo: {mode}\n")
                f.write(f"  Confiança: {decision['confidence']:.1%}\n")
                f.write(
                    f"  Votos: Split={decision['split_votes']:.1f}, Solo={decision['solo_votes']:.1f}\n"
                )
                f.write(f"  Duração: {decision['duration']:.1f}s\n")
                f.write("\n")

        print(f"📄 Relatório detalhado salvo: {output_file}")


def main():
    if not FACECUT_JSON.exists():
        print("❌ Arquivo de cortes não encontrado")
        return

    if not FACES_CACHE.exists():
        print("❌ Cache de faces não encontrado. Execute facecut.py primeiro.")
        return

    # Carrega dados
    with open(FACECUT_JSON, "r", encoding="utf-8") as f:
        cuts = json.load(f)

    with open(FACES_CACHE, "r", encoding="utf-8") as f:
        faces = json.load(f)

    # Obtém dimensões do vídeo
    cap = cv2.VideoCapture(VIDEO_PATH)
    clip_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    clip_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    print(f"🎬 Vídeo: {Path(VIDEO_PATH).name}")
    print(f"📐 Dimensões: {clip_w}x{clip_h}")
    print(f"📦 Cortes para analisar: {len(cuts)}\n")

    # Processa
    preprocessor = LayoutPreprocessor(faces, clip_w, clip_h)
    preprocessor.process_all_cuts(cuts)

    # Salva resultados
    preprocessor.save_cache()
    preprocessor.print_summary()
    preprocessor.generate_report()

    print("\n✅ Pré-processamento completo!")
    print("   Agora você pode executar render_facecuts.py para renderizar")


if __name__ == "__main__":
    main()
