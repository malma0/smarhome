"""Voice-based resident identification: recognizes *who* is speaking from
their voice alone, instead of asking for a resident_id by name every
session (see voice_app.py). This is a separate concern from speech
recognition (still Whisper via Groq) - it never looks at what was said,
only at the voice itself.

Uses Resemblyzer (github.com/resemble-ai/Resemblyzer) - a small, fully
offline, pretrained speaker-embedding model (256-dim d-vectors) bundled
with the pip package itself, no extra download step. Matching a voice is
just cosine similarity between embeddings, same idea as the persona/gender
signals elsewhere in this codebase: a v1 heuristic (a similarity threshold,
not a trained classifier) good enough for a handful of residents in one
household.

Embeddings are stored as a plain resident preference (memory.py's existing
key-value store) rather than a new table - no schema migration needed to
add this, consistent with the "general key-value, no per-preference-type
migration" design already used for style/gender signals.

The heavy model itself is loaded lazily and only touched by
embed_wav_bytes() - every other function here is pure numpy/json and fully
testable without it.
"""

import io
import json

import numpy as np

from app.memory import MemoryStore

VOICEPRINT_PREFERENCE_KEY = "voice_embedding"
DEFAULT_MATCH_THRESHOLD = 0.75

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder

        _encoder = VoiceEncoder()
    return _encoder


def embed_wav_bytes(wav_bytes: bytes) -> np.ndarray:
    """Turns raw WAV bytes (as produced by voice_app.record_until_enter)
    into a speaker-embedding vector. Needs a moment of actual speech to be
    reliable - a very short or silent clip embeds fine but won't match
    anything confidently, which is the correct behavior (see
    identify_resident).

    resemblyzer.preprocess_wav only takes a filepath or an already-decoded
    float waveform array (not a file-like object), so the WAV is decoded
    with soundfile first."""
    import soundfile as sf
    from resemblyzer import preprocess_wav

    data, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    if data.ndim > 1:  # downmix to mono if the clip isn't already
        data = data.mean(axis=1)
    wav = preprocess_wav(data, source_sr=sample_rate)
    return _get_encoder().embed_utterance(wav)


def embedding_to_json(embedding: np.ndarray) -> str:
    return json.dumps(embedding.tolist())


def embedding_from_json(data: str) -> np.ndarray:
    return np.array(json.loads(data), dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def identify_resident(
    embedding: np.ndarray,
    enrolled: dict[str, np.ndarray],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> str | None:
    """Best cosine-similarity match above threshold, or None - a stranger,
    a too-short/unclear clip, or nobody enrolled yet all look the same
    here: "I don't recognize this voice", never a low-confidence guess."""
    best_id, best_score = None, -1.0
    for resident_id, known in enrolled.items():
        score = cosine_similarity(embedding, known)
        if score > best_score:
            best_id, best_score = resident_id, score
    if best_id is not None and best_score >= threshold:
        return best_id
    return None


def load_enrolled_voiceprints(memory: MemoryStore) -> dict[str, np.ndarray]:
    """Every resident who has enrolled a voice so far - everyone else (new
    installs, or a resident who's only ever typed their name) simply isn't
    in the returned dict, which identify_resident already treats as "no
    match" rather than an error."""
    enrolled = {}
    for resident_id in memory.list_resident_ids():
        raw = memory.get_preference(resident_id, VOICEPRINT_PREFERENCE_KEY)
        if raw:
            enrolled[resident_id] = embedding_from_json(raw)
    return enrolled


def enroll_resident(memory: MemoryStore, resident_id: str, embedding: np.ndarray) -> None:
    """Overwrites any previous enrollment for this resident_id on purpose -
    re-enrolling (a noisier first sample, a changed voice) should just
    replace it, not require deleting the old one first."""
    memory.ensure_resident(resident_id)
    memory.set_preference(resident_id, VOICEPRINT_PREFERENCE_KEY, embedding_to_json(embedding))
