import json
import re
from pathlib import Path


def clean_transcript():
    path = Path("transcript_segments.json")
    if not path.exists():
        print("Arquivo não encontrado.")
        return

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cleaned = 0
    for item in data:
        original = item["text"]
        # Remove [0], [1], [ID 0], etc.
        new_text = re.sub(r"^\[\d+\]\s*", "", original)
        new_text = re.sub(r"^\[ID\s*\d+\]\s*", "", new_text)

        if original != new_text:
            item["text"] = new_text
            cleaned += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Limpeza concluída. {cleaned} segmentos limpos.")


if __name__ == "__main__":
    clean_transcript()
