from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from db_utils import load_env_file

try:
    from google import genai
    from google.genai import errors as genai_errors
except ImportError:  # pragma: no cover - handled at runtime with a clear error.
    genai = None
    genai_errors = None


DEFAULT_STT_MODEL = "gemini-2.0-flash"
DEFAULT_TRANSCRIPT_PROMPT = (
    "Generate a clean French transcript of the speech only. "
    "Do not summarize. Do not translate. "
    "Preserve academic codes exactly when possible, such as GSI, GSIL, INFO, MECA, UE, TD, TP."
)


class STTApiError(RuntimeError):
    pass


def guess_mime_type(audio_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(audio_path))
    return mime_type or "audio/wav"


def build_genai_client() -> Any:
    if genai is None:
        raise RuntimeError(
            "Le package 'google-genai' n'est pas installe. "
            "Installez-le avec: pip install google-genai"
        )

    env = load_env_file()
    api_key = env.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY est introuvable dans .env")

    return genai.Client(api_key=api_key)


def transcribe_audio_file(
    audio_path: Path,
    model: str = DEFAULT_STT_MODEL,
    prompt: str = DEFAULT_TRANSCRIPT_PROMPT,
) -> str:
    if not audio_path.exists():
        raise FileNotFoundError(f"Fichier audio introuvable: {audio_path}")

    client = build_genai_client()
    mime_type = guess_mime_type(audio_path)

    uploaded = client.files.upload(
        file=str(audio_path),
        config={"mime_type": mime_type},
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=[prompt, uploaded],
        )
    except Exception as exc:
        raise STTApiError(format_genai_error(exc, model=model)) from exc

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("La reponse Gemini ne contient pas de texte exploitable.")
    return text.strip()


def format_genai_error(exc: Exception, model: str) -> str:
    status_code = getattr(exc, "status_code", None)
    response_json = getattr(exc, "response_json", None) or {}
    error = response_json.get("error", {}) if isinstance(response_json, dict) else {}
    message = error.get("message") or str(exc)
    status = error.get("status")

    if status_code == 429 or status == "RESOURCE_EXHAUSTED":
        retry_delay = extract_retry_delay(error)
        suffix = f" Reessayez dans environ {retry_delay}." if retry_delay else ""
        return (
            f"Quota Gemini depasse pour le modele {model}.{suffix}\n"
            "Votre code et votre cle fonctionnent, mais ce projet n'a pas de quota disponible actuellement.\n"
            "Solutions: attendre, changer de modele, utiliser une autre cle/projet, ou activer/verifier la facturation."
        )

    if status_code == 503 or status == "UNAVAILABLE":
        return (
            f"Le modele Gemini {model} est temporairement indisponible ou surcharge.\n"
            "Reessayez dans quelques minutes ou testez un autre modele."
        )

    if status_code in {400, 401, 403}:
        return (
            f"Erreur d'autorisation ou de configuration Gemini ({status_code}).\n"
            f"Message API: {message}"
        )

    return f"Erreur Gemini avec le modele {model}: {message}"


def extract_retry_delay(error: dict[str, Any]) -> str | None:
    for detail in error.get("details", []):
        if not isinstance(detail, dict):
            continue
        retry_delay = detail.get("retryDelay")
        if retry_delay:
            return retry_delay
    return None
