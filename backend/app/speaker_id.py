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
household - it will never be "always correct" (no voice-biometric system
is), but multi-sample matching below makes it meaningfully more robust
than one recording ever can be.

A resident's profile is a small *set* of past embeddings (up to
MAX_ENROLLED_SAMPLES, oldest dropped first), not a single vector: matching
takes the best similarity across all of a resident's stored samples, since
any one recording can be short, noisy, or just an atypical read of a
phrase. voice_app.py also feeds every confident match back in as a new
sample (see enroll_resident call there) - the profile keeps quietly
absorbing real usage instead of staying frozen at whatever the first,
one-shot enrollment happened to sound like.

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
MAX_ENROLLED_SAMPLES = 5

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


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _samples_from_json(data: str) -> list[np.ndarray]:
    """Backward compatible with the very first version of this file, which
    stored a single embedding as a flat list of floats: that decodes here
    as one sample. Current profiles store a list of samples (a list of
    lists)."""
    parsed = json.loads(data)
    if parsed and isinstance(parsed[0], list):
        return [np.array(sample, dtype=np.float32) for sample in parsed]
    return [np.array(parsed, dtype=np.float32)]


def _samples_to_json(samples: list[np.ndarray]) -> str:
    return json.dumps([sample.tolist() for sample in samples])


def identify_resident(
    embedding: np.ndarray,
    enrolled: dict[str, list[np.ndarray]],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> str | None:
    """Best cosine-similarity match (against any one of a resident's stored
    samples, not an average of them - a single close match is a stronger
    signal than blending it away) above threshold, or None - a stranger, a
    too-short/unclear clip, or nobody enrolled yet all look the same here:
    "I don't recognize this voice", never a low-confidence guess."""
    best_id, best_score = None, -1.0
    for resident_id, samples in enrolled.items():
        score = max(cosine_similarity(embedding, sample) for sample in samples)
        if score > best_score:
            best_id, best_score = resident_id, score
    if best_id is not None and best_score >= threshold:
        return best_id
    return None


def load_enrolled_voiceprints(memory: MemoryStore) -> dict[str, list[np.ndarray]]:
    """Every resident who has enrolled a voice so far - everyone else (new
    installs, or a resident who's only ever typed their name) simply isn't
    in the returned dict, which identify_resident already treats as "no
    match" rather than an error."""
    enrolled = {}
    for resident_id in memory.list_resident_ids():
        raw = memory.get_preference(resident_id, VOICEPRINT_PREFERENCE_KEY)
        if raw:
            enrolled[resident_id] = _samples_from_json(raw)
    return enrolled


def enroll_resident(
    memory: MemoryStore,
    resident_id: str,
    embedding: np.ndarray,
    max_samples: int = MAX_ENROLLED_SAMPLES,
) -> None:
    """Adds a new sample to this resident's profile - never overwrites the
    existing ones outright, since a profile built from several samples
    matches far more reliably than any single recording. Keeps only the
    most recent max_samples, oldest dropped first, so the profile can also
    drift with a voice that gradually changes rather than being stuck
    with a sample from months ago."""
    memory.ensure_resident(resident_id)
    existing_raw = memory.get_preference(resident_id, VOICEPRINT_PREFERENCE_KEY)
    samples = _samples_from_json(existing_raw) if existing_raw else []
    samples.append(embedding)
    samples = samples[-max_samples:]
    memory.set_preference(resident_id, VOICEPRINT_PREFERENCE_KEY, _samples_to_json(samples))
