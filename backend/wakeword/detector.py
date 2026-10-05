"""The own wake-word model at work: audio -> openWakeWord's speech embeddings
(a 96-number vector every 80 ms) -> a small network over the last 16 of them
(1.28 s) -> how sure it is that "Джарвис" just ended.

Used by training (wakeword/train.py) and by Jarvis on each phrase the
microphone hears - one place, so both see the audio the same way.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

RATE = 16000
WINDOW_FRAMES = 16  # embedding frames the model sees: 16 x 80 ms
FRAME_SECONDS = 0.08
FIRST_FRAME_END = 0.76  # the first embedding frame covers the clip's first 0.76 s
LEAD_IN = np.zeros(RATE, dtype=np.int16)  # 1 s of silence in front: a name at the very start still gets a window
MODEL_PATH = Path(__file__).resolve().parent / "models" / "jarvis.pt"

_features = None


def features():
    """openWakeWord's shared melspectrogram + embedding models (onnx, CPU), loaded once."""
    global _features
    if _features is None:
        from openwakeword.utils import AudioFeatures, download_models

        download_models(model_names=["__none__"])  # just the shared feature models, ~3 MB, once
        _features = AudioFeatures(inference_framework="onnx")
    return _features


def embed(audio: np.ndarray) -> np.ndarray:
    """int16 mono 16 kHz -> (frames, 96), with LEAD_IN in front."""
    clip = np.concatenate([LEAD_IN, audio.astype(np.int16)])
    return features().embed_clips(clip[None, :], batch_size=1)[0]


def frame_ending_at(seconds_into_audio: float) -> int:
    """The embedding frame whose window ends closest to this moment of the (un-padded) audio."""
    t = seconds_into_audio + len(LEAD_IN) / RATE
    return max(WINDOW_FRAMES - 1, round((t - FIRST_FRAME_END) / FRAME_SECONDS))


def windows(embedding: np.ndarray) -> np.ndarray:
    """Every 16-frame window, one per 80 ms: (n, 16, 96)."""
    n = embedding.shape[0] - WINDOW_FRAMES + 1
    if n <= 0:
        pad = np.zeros((WINDOW_FRAMES - embedding.shape[0], embedding.shape[1]), dtype=embedding.dtype)
        return np.concatenate([pad, embedding])[None]
    return np.stack([embedding[i:i + WINDOW_FRAMES] for i in range(n)])


def build_model():
    import torch.nn as nn

    # LayerNorm first: without it the embeddings' scale killed every ReLU of a small net and it said 0.611
    # to everything. The size is for 2000 hours of other audio as negatives (train.py) - on the residents'
    # few hundred takes alone any net learned them by heart (437 false wakes an hour on new audio).
    return nn.Sequential(nn.Flatten(), nn.LayerNorm(WINDOW_FRAMES * 96), nn.Linear(WINDOW_FRAMES * 96, 128),
                         nn.ReLU(), nn.Dropout(0.3), nn.Linear(128, 64), nn.ReLU(), nn.Linear(64, 1))


class WakeModel:
    """A trained model and its threshold: score(audio) is the best moment's probability."""

    def __init__(self, path: Path = MODEL_PATH):
        import torch

        saved = torch.load(path, map_location="cpu", weights_only=False)
        self.net = build_model()
        self.net.load_state_dict(saved["state"])
        self.net.eval()
        self.threshold = float(saved["threshold"])
        self.info = saved.get("info", {})

    def scores(self, audio: np.ndarray) -> np.ndarray:
        import torch

        with torch.no_grad():
            x = torch.from_numpy(windows(embed(audio)).astype(np.float32))
            return torch.sigmoid(self.net(x)).numpy()[:, 0]

    def score(self, audio: np.ndarray) -> float:
        return float(self.scores(audio).max())

    def heard(self, audio: np.ndarray) -> bool:
        return self.score(audio) >= self.threshold
