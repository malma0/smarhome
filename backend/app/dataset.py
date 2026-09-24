"""Every voice utterance kept as training data - the audio, what Whisper
heard, and the resident's correction if it heard wrong - for fine-tuning
speech recognition later. Without real recordings of real use there's
nothing to train on, so collection starts now, long before any training.

Layout is Hugging Face "audiofolder"-compatible: WAV files under audio/,
one metadata.jsonl line per utterance whose file_name is relative to the
dataset folder, so `load_dataset("audiofolder", data_dir=...)` reads it
as-is. For training, use corrected_text when present, transcript otherwise.

This is the resident's own voice - personal data. The folder is gitignored
and never leaves this machine.
"""

import io
import json
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


class UtteranceLog:
    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._metadata = self._root / "metadata.jsonl"

    def log(
        self,
        *,
        wav_bytes: bytes,
        resident_id: str,
        transcript: str | None,
        response: str | None = None,
    ) -> str:
        """transcript is None when transcription itself failed (a network
        error), "" when Whisper returned nothing - both still worth keeping,
        the audio is real and can be corrected by hand."""
        now = datetime.now(timezone.utc)
        utterance_id = f"{now:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"
        file_name = f"audio/{utterance_id}.wav"

        (self._root / "audio").mkdir(parents=True, exist_ok=True)
        (self._root / file_name).write_bytes(wav_bytes)

        record = {
            "file_name": file_name,
            "id": utterance_id,
            "created_at": now.isoformat(timespec="seconds"),
            "resident_id": resident_id,
            "duration_seconds": round(_wav_duration_seconds(wav_bytes), 2),
            "transcript": transcript,
            "corrected_text": None,
            "response": response,
        }
        with self._metadata.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return utterance_id

    def _read_records(self) -> list[dict]:
        if not self._metadata.exists():
            return []
        return [json.loads(line) for line in self._metadata.read_text(encoding="utf-8").splitlines() if line.strip()]

    def correct(self, utterance_id: str, corrected_text: str) -> bool:
        """Rewrites the whole metadata file - fine at the scale of one
        household's recordings, and keeps the file a plain audiofolder
        metadata.jsonl rather than an append-only log of edits."""
        records = self._read_records()
        for record in records:
            if record["id"] == utterance_id:
                record["corrected_text"] = corrected_text
                break
        else:
            return False
        self._metadata.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )
        return True

    def stats(self) -> dict:
        records = self._read_records()
        return {
            "utterances": len(records),
            "minutes": round(sum(r.get("duration_seconds", 0) for r in records) / 60, 1),
            "corrected": sum(1 for r in records if r.get("corrected_text")),
        }
