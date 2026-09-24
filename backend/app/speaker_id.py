"""Voice-based resident identification: recognizes *who* is speaking from
their voice alone, instead of asking for a resident_id by name every
session (see voice_app.py). This is a separate concern from speech
recognition (still Whisper via Groq) - it never looks at what was said,
only at the voice itself.

Two pretrained, fully offline speaker-embedding models (see MODELS):
ECAPA-TDNN from SpeechBrain (the default, SPEAKER_MODEL=ecapa) and the
older, lighter Resemblyzer. Matching a voice is cosine similarity between
embeddings with measured thresholds - not a trained classifier yet (the
recordings kept in backend/voiceprints/ are the data for one). It will
never be "always correct" - no voice-biometric system is, least of all on
a one-second phrase - but multi-sample matching makes it meaningfully more
robust than one recording ever can be.

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
import itertools
import json
import re
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.memory import MemoryStore

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SpeakerModel:
    """Embeddings from different models aren't comparable, so each model
    has its own profile storage (preference key) and its own thresholds."""

    name: str
    preference_key: str
    threshold: float  # confident match
    relative_min: float  # see decide()
    relative_margin: float


MODELS = {
    # Resemblyzer (2019, 256-dim). From real recordings on this laptop's
    # mic: longer phrases scored 0.73-0.78 against their own profile, other
    # voices at most 0.56 - but on 1 s crops the ranges overlap completely
    # (own as low as 0.53, others as high as 0.70). Kept for comparison and
    # for profiles made before ECAPA.
    "resemblyzer": SpeakerModel("resemblyzer", "voice_embedding", 0.70, 0.45, 0.10),
    # ECAPA-TDNN (SpeechBrain, 192-dim, Apache-2.0). Same test set, short
    # crops of five voices (this household's real recordings plus two
    # audiobook readers and two synthetic voices): on 1 s, 100% picked the
    # right person vs Resemblyzer's 91%; own/other scores 5th/95th
    # percentile 0.44/0.31 (Resemblyzer 0.58/0.67 - fully overlapped); on
    # 2 s 0.57 vs at most 0.47. Scores run lower overall, hence the lower
    # thresholds. Not yet measured on several *male* voices side by side.
    "ecapa": SpeakerModel("ecapa", "voice_embedding:ecapa", 0.50, 0.30, 0.10),
}

_active = MODELS["resemblyzer"]


def configure(model_name: str) -> SpeakerModel:
    """Picks the model for this process (voice_app does this at startup,
    from SPEAKER_MODEL). Default stays Resemblyzer so nothing changes for
    code that never configures it."""
    global _active
    if model_name not in MODELS:
        raise ValueError(f"Unknown speaker model {model_name!r}. Valid: {sorted(MODELS)}")
    _active = MODELS[model_name]
    return _active


def active_model() -> SpeakerModel:
    return _active


# Kept for callers/tests that predate per-model settings.
VOICEPRINT_PREFERENCE_KEY = MODELS["resemblyzer"].preference_key
DEFAULT_MATCH_THRESHOLD = MODELS["resemblyzer"].threshold
# More than a handful: with deliberate enrollment ("Записать голос", 3
# samples at a time) plus "Дозаписать", a profile would otherwise churn
# through its own fresh samples.
MAX_ENROLLED_SAMPLES = 10

# Measured on this laptop's hybrid CPU (Core Ultra 9 185H, 22 threads):
# one Resemblyzer embedding took 1100-1900 ms with PyTorch's default thread
# count (16-22) and 30-60 ms with 12 or fewer - every step waits for its
# slowest thread, and some land on the low-power cores. ECAPA likewise:
# 687 ms at the default vs 422 ms at 4 for a 3.6 s phrase. Process-wide,
# but nothing else in Jarvis's process uses PyTorch (Voicebox runs as its
# own server).
TORCH_THREADS = 4

ECAPA_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_DIR = BACKEND_DIR / "models" / "spkrec-ecapa-voxceleb"

_encoder = None
_ecapa = None
# The background warm-up at startup and the first real phrase can both get
# here at once; without the lock both loaded the model, competing for the
# CPU - a live test measured 61 s instead of ~6 s.
_encoder_lock = threading.Lock()


def _get_encoder():
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            import torch
            from resemblyzer import VoiceEncoder

            torch.set_num_threads(TORCH_THREADS)
            _encoder = VoiceEncoder()
    return _encoder


def _get_ecapa():
    """Loads from backend/models/ once downloaded (no network after that).
    COPY, not the default symlinks: symlinks on Windows need admin rights
    or Developer Mode."""
    global _ecapa
    with _encoder_lock:
        if _ecapa is None:
            import torch
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy

            torch.set_num_threads(TORCH_THREADS)
            source = str(ECAPA_DIR) if (ECAPA_DIR / "hyperparams.yaml").exists() else ECAPA_SOURCE
            _ecapa = EncoderClassifier.from_hparams(
                source=source,
                savedir=str(ECAPA_DIR),
                run_opts={"device": "cpu"},
                local_strategy=LocalStrategy.COPY,
            )
    return _ecapa


def warm_up() -> None:
    """Loads the model (~3s) and runs one throwaway embedding - the first
    one measured 4.3s on its own (one-time setup inside the libraries) vs
    ~0.05s after. Meant for a background thread at startup, so the first
    real phrase doesn't pay for it."""
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 3000, 16000)).astype(np.int16)  # 1s - silence would be trimmed to nothing
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(noise.tobytes())
    embed_wav_bytes(buffer.getvalue())


def _decode(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    if data.ndim > 1:  # downmix to mono if the clip isn't already
        data = data.mean(axis=1)
    return data, sample_rate


def embed_wav_bytes(wav_bytes: bytes) -> np.ndarray:
    """Turns raw WAV bytes (as produced by voice_app.record_until_enter)
    into a speaker-embedding vector with the active model. Needs a moment
    of actual speech to be reliable - a very short or silent clip embeds
    fine but won't match anything confidently, which is the correct
    behavior (see decide)."""
    data, sample_rate = _decode(wav_bytes)
    if _active.name == "ecapa":
        return _embed_ecapa(data, sample_rate)
    # resemblyzer.preprocess_wav takes a filepath or a decoded float
    # waveform (not a file-like object), and resamples/trims itself.
    from resemblyzer import preprocess_wav

    return _get_encoder().embed_utterance(preprocess_wav(data, source_sr=sample_rate))


def _embed_ecapa(data: np.ndarray, sample_rate: int) -> np.ndarray:
    import torch

    if sample_rate != 16000:  # ECAPA was trained on 16 kHz (the mic already records at 16 kHz)
        import librosa

        data = librosa.resample(data, orig_sr=sample_rate, target_sr=16000)
    with torch.no_grad():
        embedding = _get_ecapa().encode_batch(torch.from_numpy(np.ascontiguousarray(data)).unsqueeze(0))
    return embedding.squeeze().numpy().astype(np.float32)


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


def rank_residents(embedding: np.ndarray, enrolled: dict[str, list[np.ndarray]]) -> list[tuple[str, float]]:
    """Every enrolled resident with their best score (against any one of
    their samples, not an average - a single close match is a stronger
    signal than blending it away), best first."""
    ranked = [
        (resident_id, max(cosine_similarity(embedding, sample) for sample in samples))
        for resident_id, samples in enrolled.items()
    ]
    return sorted(ranked, key=lambda item: item[1], reverse=True)


def decide(
    ranked: list[tuple[str, float]],
    threshold: float | None = None,
    relative_min: float | None = None,
    relative_margin: float | None = None,
) -> tuple[str | None, bool]:
    """(resident, confident). Confident: the best score clears the
    threshold. Not confident but still chosen: with two or more voices
    enrolled, the best is at least relative_min and beats the runner-up by
    relative_margin - good enough to say who's talking, not to teach their
    profile. From real recordings: a ~1 s phrase scored only 0.60 against
    its own (male) speaker's Resemblyzer profile - under the threshold - but
    0.28-0.45 against female voices; too little voice to be *sure*, plenty
    to tell which of two very different voices it's nearer. Otherwise
    (None, False): "I don't recognize this voice", never a coin toss between
    two close candidates. Unset parameters come from the active model."""
    threshold = _active.threshold if threshold is None else threshold
    relative_min = _active.relative_min if relative_min is None else relative_min
    relative_margin = _active.relative_margin if relative_margin is None else relative_margin
    if not ranked:
        return None, False
    best_id, best_score = ranked[0]
    if best_score >= threshold:
        return best_id, True
    if len(ranked) >= 2 and best_score >= relative_min and best_score - ranked[1][1] >= relative_margin:
        return best_id, False
    return None, False


def match_resident(
    embedding: np.ndarray, enrolled: dict[str, list[np.ndarray]], threshold: float | None = None
) -> tuple[str | None, bool]:
    return decide(rank_residents(embedding, enrolled), threshold)


def identify_resident(
    embedding: np.ndarray,
    enrolled: dict[str, list[np.ndarray]],
    threshold: float | None = None,
) -> str | None:
    """Confident matches only - see match_resident for the full decision."""
    resident, confident = match_resident(embedding, enrolled, threshold)
    return resident if confident else None


def load_enrolled_voiceprints(memory: MemoryStore) -> dict[str, list[np.ndarray]]:
    """Every resident with a voice profile for the active model - everyone
    else (new installs, a resident who's only ever typed their name, or one
    enrolled only under another model) simply isn't in the returned dict,
    which decide() already treats as "no match" rather than an error."""
    enrolled = {}
    for resident_id in memory.list_resident_ids():
        raw = memory.get_preference(resident_id, _active.preference_key)
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
    existing_raw = memory.get_preference(resident_id, _active.preference_key)
    samples = _samples_from_json(existing_raw) if existing_raw else []
    samples.append(embedding)
    samples = samples[-max_samples:]
    memory.set_preference(resident_id, _active.preference_key, _samples_to_json(samples))


# --- the audio behind every voice sample ---
#
# A profile only keeps embeddings, which are tied to one model and can't be
# turned back into audio. The recordings themselves are kept too, so that
# profiles can be rebuilt for a better model later (rebuild_profiles), and
# so there's labeled data to train a household-specific model on. Residents'
# own voices - personal data: backend/voiceprints/ is gitignored and never
# leaves the machine.

VOICEPRINTS_DIR = BACKEND_DIR / "voiceprints"


def _folder_name(resident_id: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", resident_id).strip(" .") or "_"


# Enrollment saves several pieces within milliseconds; a clock-based name
# alone collided on Windows (coarse timer) and silently overwrote samples.
_save_counter = itertools.count()


def save_sample_audio(resident_id: str, wav_bytes: bytes, root: Path | None = None) -> Path:
    folder = (root or VOICEPRINTS_DIR) / _folder_name(resident_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%dT%H%M%S')}_{next(_save_counter):06d}"
    path = folder / f"{stem}.wav"
    while path.exists():  # another process saved in the same second
        path = folder / f"{stem}_{uuid.uuid4().hex[:6]}.wav"
    path.write_bytes(wav_bytes)
    return path


def sample_audio(root: Path | None = None) -> dict[str, list[Path]]:
    """Resident folder name -> their saved recordings, oldest first."""
    base = root or VOICEPRINTS_DIR
    if not base.exists():
        return {}
    return {d.name: sorted(d.glob("*.wav")) for d in sorted(base.iterdir()) if d.is_dir() and any(d.glob("*.wav"))}


def rebuild_profiles(memory: MemoryStore, root: Path | None = None, max_samples: int = MAX_ENROLLED_SAMPLES) -> dict[str, int]:
    """Re-embeds every resident's saved recordings with the active model and
    replaces their profile for it (latest max_samples). Returns resident ->
    samples used."""
    rebuilt = {}
    for resident_id, paths in sample_audio(root).items():
        embeddings = [embed_wav_bytes(p.read_bytes()) for p in paths[-max_samples:]]
        memory.ensure_resident(resident_id)
        memory.set_preference(resident_id, _active.preference_key, _samples_to_json(embeddings))
        rebuilt[resident_id] = len(embeddings)
    return rebuilt
