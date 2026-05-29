from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from db_utils import load_env_file


ASSEMBLYAI_BASE_URL = "https://api.assemblyai.com/v2"
DEFAULT_LANGUAGE_CODE = "fr"
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_SPEECH_MODEL = "universal-2"

KEYTERMS_PROMPT = [
    "GSI",
    "GSIL",
    "INFO",
    "MECA",
    "infotronique",
    "informatique",
    "mecatronique",
    "industrielle",
    "emploi du temps",
    "Wiem Arfaoui",
    "Iheb Ajengui",
    "Maha Dridi",
    "RTOS",
    "developpement mobile",
    "linux embarque",
    "intelligence artificielle",
]


class AssemblyAIError(RuntimeError):
    pass


def get_api_key() -> str:
    env = load_env_file()
    api_key = env.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise AssemblyAIError("ASSEMBLYAI_API_KEY est introuvable dans .env")
    return api_key


def auth_headers(api_key: str) -> dict[str, str]:
    return {"authorization": api_key}


def upload_audio(audio_path: Path, api_key: str | None = None) -> str:
    if not audio_path.exists():
        raise FileNotFoundError(f"Fichier audio introuvable: {audio_path}")

    key = api_key or get_api_key()
    headers = auth_headers(key)

    with audio_path.open("rb") as audio_file:
        response = requests.post(
            f"{ASSEMBLYAI_BASE_URL}/upload",
            headers=headers,
            data=audio_file,
            timeout=120,
        )

    if response.status_code >= 400:
        raise AssemblyAIError(format_assemblyai_error(response, action="upload audio"))

    upload_url = response.json().get("upload_url")
    if not upload_url:
        raise AssemblyAIError("AssemblyAI n'a pas retourne upload_url.")
    return upload_url


def create_transcript(
    upload_url: str,
    api_key: str | None = None,
    language_code: str = DEFAULT_LANGUAGE_CODE,
    keyterms_prompt: list[str] | None = None,
    speech_model: str = DEFAULT_SPEECH_MODEL,
) -> str:
    key = api_key or get_api_key()
    headers = {
        **auth_headers(key),
        "content-type": "application/json",
    }
    payload: dict[str, Any] = {
        "audio_url": upload_url,
        "language_code": language_code,
        "speech_model": speech_model,
    }

    terms = keyterms_prompt if keyterms_prompt is not None else KEYTERMS_PROMPT
    if terms:
        payload["keyterms_prompt"] = terms

    response = requests.post(
        f"{ASSEMBLYAI_BASE_URL}/transcript",
        headers=headers,
        json=payload,
        timeout=60,
    )

    if response.status_code >= 400:
        raise AssemblyAIError(format_assemblyai_error(response, action="create transcript"))

    transcript_id = response.json().get("id")
    if not transcript_id:
        raise AssemblyAIError("AssemblyAI n'a pas retourne d'identifiant de transcription.")
    return transcript_id


def poll_transcript(
    transcript_id: str,
    api_key: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    key = api_key or get_api_key()
    headers = auth_headers(key)
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        response = requests.get(
            f"{ASSEMBLYAI_BASE_URL}/transcript/{transcript_id}",
            headers=headers,
            timeout=60,
        )

        if response.status_code >= 400:
            raise AssemblyAIError(format_assemblyai_error(response, action="poll transcript"))

        data = response.json()
        status = data.get("status")
        if status == "completed":
            return data
        if status == "error":
            error = data.get("error") or "Erreur AssemblyAI inconnue."
            raise AssemblyAIError(f"AssemblyAI a echoue: {error}")

        time.sleep(poll_interval_seconds)

    raise AssemblyAIError(
        f"Timeout AssemblyAI: transcription non terminee apres {timeout_seconds:.0f}s."
    )


def transcribe_audio_file(
    audio_path: Path,
    language_code: str = DEFAULT_LANGUAGE_CODE,
    keyterms_prompt: list[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    speech_model: str = DEFAULT_SPEECH_MODEL,
) -> str:
    api_key = get_api_key()
    upload_url = upload_audio(audio_path, api_key=api_key)
    transcript_id = create_transcript(
        upload_url,
        api_key=api_key,
        language_code=language_code,
        keyterms_prompt=keyterms_prompt,
        speech_model=speech_model,
    )
    result = poll_transcript(
        transcript_id,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    text = result.get("text")
    if not text:
        raise AssemblyAIError("AssemblyAI n'a pas retourne de texte de transcription.")
    return str(text).strip()


def format_assemblyai_error(response: requests.Response, action: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": response.text}

    message = payload.get("error") or payload.get("message") or str(payload)
    if response.status_code == 401:
        return f"Erreur AssemblyAI pendant {action}: cle API invalide ou manquante."
    if response.status_code == 402:
        return f"Erreur AssemblyAI pendant {action}: quota ou credits insuffisants."
    if response.status_code == 429:
        return f"Erreur AssemblyAI pendant {action}: rate limit depasse. Reessayez plus tard."
    return f"Erreur AssemblyAI pendant {action} ({response.status_code}): {message}"
