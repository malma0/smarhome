import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    anthropic_api_key: str
    anthropic_model: str
    ollama_model: str
    ollama_base_url: str
    groq_api_key: str
    groq_model: str
    groq_base_url: str
    groq_temperature: float
    home_assistant_url: str
    home_assistant_token: str
    db_path: str
    port: int
    tts_provider: str
    edge_tts_voice: str
    piper_model_path: str
    piper_config_path: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model: str
    voicebox_base_url: str
    voicebox_profile: str
    voicebox_engine: str
    voicebox_language: str
    voicebox_reference_wav: str
    voicebox_russian_stress: bool
    ruaccent_model: str
    tts_enabled: bool
    voice_id_enabled: bool
    voice_id_threshold: float | None
    speaker_model: str
    whisper_vocabulary: str
    dataset_enabled: bool
    dataset_dir: str
    voice_mode: str
    wake_words: str
    vosk_model_path: str
    wake_listen_seconds: float
    follow_up_seconds: float


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.environ.get("LLM_PROVIDER", "claude"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_base_url=os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        groq_temperature=float(os.environ.get("GROQ_TEMPERATURE", "0.4")),
        home_assistant_url=os.environ.get("HOME_ASSISTANT_URL", "http://localhost:8123").rstrip("/"),
        home_assistant_token=os.environ.get("HOME_ASSISTANT_TOKEN", ""),
        db_path=os.environ.get("JARVIS_DB_PATH", "jarvis.db"),
        port=int(os.environ.get("JARVIS_PORT", "8000")),
        tts_provider=os.environ.get("TTS_PROVIDER", "edge"),
        edge_tts_voice=os.environ.get("EDGE_TTS_VOICE", "ru-RU-DmitryNeural"),
        piper_model_path=os.environ.get("PIPER_MODEL_PATH", "voices/ru_RU-irina-medium.onnx"),
        piper_config_path=os.environ.get("PIPER_CONFIG_PATH", "voices/ru_RU-irina-medium.onnx.json"),
        elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=os.environ.get("ELEVENLABS_VOICE_ID", ""),
        elevenlabs_model=os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        voicebox_base_url=os.environ.get("VOICEBOX_BASE_URL", "http://127.0.0.1:8000"),
        voicebox_profile=os.environ.get("VOICEBOX_PROFILE", ""),
        voicebox_engine=os.environ.get("VOICEBOX_ENGINE", "chatterbox"),
        voicebox_language=os.environ.get("VOICEBOX_LANGUAGE", "ru"),
        voicebox_reference_wav=os.environ.get("VOICEBOX_REFERENCE_WAV", ""),
        voicebox_russian_stress=os.environ.get("VOICEBOX_RUSSIAN_STRESS", "true").strip().lower()
        not in ("false", "0", "no"),
        ruaccent_model=os.environ.get("RUACCENT_MODEL", "tiny2.1"),
        tts_enabled=os.environ.get("JARVIS_TTS_ENABLED", "true").strip().lower() not in ("false", "0", "no"),
        voice_id_enabled=os.environ.get("VOICE_ID_ENABLED", "true").strip().lower() not in ("false", "0", "no"),
        # Empty = the chosen model's own measured threshold (app.speaker_id.MODELS).
        voice_id_threshold=float(os.environ["VOICE_ID_THRESHOLD"]) if os.environ.get("VOICE_ID_THRESHOLD") else None,
        speaker_model=os.environ.get("SPEAKER_MODEL", "ecapa").strip().lower(),
        whisper_vocabulary=os.environ.get("WHISPER_VOCABULARY", ""),
        dataset_enabled=os.environ.get("JARVIS_DATASET_ENABLED", "true").strip().lower() not in ("false", "0", "no"),
        dataset_dir=os.environ.get("JARVIS_DATASET_DIR", "dataset"),
        voice_mode=os.environ.get("VOICE_MODE", "wake").strip().lower(),
        wake_words=os.environ.get("WAKE_WORDS", "джарвис,джервис"),
        vosk_model_path=os.environ.get("VOSK_MODEL_PATH", "models/vosk-model-small-ru-0.22"),
        wake_listen_seconds=float(os.environ.get("WAKE_LISTEN_SECONDS", "8")),
        follow_up_seconds=float(os.environ.get("FOLLOW_UP_SECONDS", "10")),
    )


settings = load_settings()
