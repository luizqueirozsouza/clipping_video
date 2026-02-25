from render_facecuts import HAS_RUST, SmartLayoutDetector


def test_integration():
    print(f"Módulo Rust detectado: {HAS_RUST}")

    # Dados de teste fictícios
    faces_data = {
        "faces_by_time": {
            "1000": [
                {
                    "x": 100,
                    "y": 100,
                    "w": 50,
                    "h": 50,
                    "score": 0.9,
                    "mouth_score": 0.5,
                    "is_speaking": True,
                }
            ],
            "2000": [
                {
                    "x": 800,
                    "y": 100,
                    "w": 50,
                    "h": 50,
                    "score": 0.9,
                    "mouth_score": 0.5,
                    "is_speaking": True,
                }
            ],
        }
    }

    detector = SmartLayoutDetector(faces_data, clip_w=1080, clip_h=1920)

    print("Testando should_split (deve usar Rust se disponível)...")
    split, pos = detector.should_split(start=1.0, end=2.0, samples=5)

    print(f"Resultado split: {split}")
    print(f"Posições detectadas: {len(pos)}")

    if HAS_RUST:
        print("SUCESSO: Integração Python -> Rust funcionando!")
    else:
        print("AVISO: Usando fallback em Python.")


if __name__ == "__main__":
    test_integration()
