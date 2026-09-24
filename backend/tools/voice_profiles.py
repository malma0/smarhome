"""Voice profile maintenance.

    python tools/voice_profiles.py stats
        who has a profile (for the chosen model) and how much saved audio

    python tools/voice_profiles.py import-dataset
        copies phrases from the speech dataset that Jarvis attributed to a
        resident into backend/voiceprints/ - a head start for residents
        whose profile predates saving sample audio

    python tools/voice_profiles.py rebuild [--model ecapa|resemblyzer]
        re-embeds everyone's saved audio with that model and replaces their
        profile for it - after switching models, or to start clean

Run from backend/. Defaults to SPEAKER_MODEL from .env.
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import speaker_id  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect  # noqa: E402
from app.memory import MemoryStore  # noqa: E402


def import_dataset(dataset_dir: Path) -> dict[str, int]:
    metadata = dataset_dir / "metadata.jsonl"
    if not metadata.exists():
        return {}
    imported: dict[str, int] = {}
    existing = {p.read_bytes() for paths in speaker_id.sample_audio().values() for p in paths}
    for line in metadata.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        resident = record.get("resident_id")
        if not resident or resident == "default":
            continue  # nobody was identified - can't know whose voice it is
        audio = (dataset_dir / record["file_name"]).read_bytes()
        if audio in existing:
            continue
        speaker_id.save_sample_audio(resident, audio)
        imported[resident] = imported.get(resident, 0) + 1
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["stats", "import-dataset", "rebuild"])
    parser.add_argument("--model", default=settings.speaker_model, choices=sorted(speaker_id.MODELS))
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    speaker_id.configure(args.model)
    memory = MemoryStore(connect(str(BACKEND / settings.db_path)))

    if args.command == "import-dataset":
        imported = import_dataset(BACKEND / settings.dataset_dir)
        print("imported:", imported or "nothing new")
    elif args.command == "rebuild":
        print(f"rebuilt ({args.model}):", speaker_id.rebuild_profiles(memory) or "no saved audio")

    print(f"\nprofiles for {args.model}:")
    for name, samples in sorted(speaker_id.load_enrolled_voiceprints(memory).items()):
        print(f"  {name}: {len(samples)} samples")
    print("saved audio:")
    for name, paths in speaker_id.sample_audio().items():
        print(f"  {name}: {len(paths)} recordings")


if __name__ == "__main__":
    main()
