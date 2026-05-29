from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

import psycopg
from faster_whisper import WhisperModel

from intent_router import parse_question
from speech_entity_linker import build_resolution_message, connect_db, speech_linker_pipeline


DEFAULT_MODEL_SIZE = "base"
DEFAULT_LANGUAGE = "fr"
DEFAULT_COMPUTE_TYPE = "int8"


def load_model(model_size: str = DEFAULT_MODEL_SIZE) -> WhisperModel:
    return WhisperModel(
        model_size,
        device="cpu",
        compute_type=DEFAULT_COMPUTE_TYPE,
    )


def build_initial_prompt(conn: psycopg.Connection | None = None) -> str:
    return (
        "Contexte universitaire. "
        "Conserver les codes groupes tels quels: GSI, GSIL, INFO, MECA. "
        "Question courte en francais."
    )


def clean_transcription_text(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return cleaned

    repeated_group = re.search(
        r"\b(?P<group>(?:GSI|GSIL|INFO|MECA)\s*[123]\s*[A-D])\b(?:\s*,?\s*(?P=group)\b){2,}",
        cleaned,
        flags=re.IGNORECASE,
    )
    if repeated_group:
        return repeated_group.group("group").upper().replace("  ", " ")

    parts = [part.strip(" ,.") for part in cleaned.split(",") if part.strip(" ,.")]
    if len(parts) >= 4:
        unique_parts: list[str] = []
        for part in parts:
            if not unique_parts or unique_parts[-1].lower() != part.lower():
                unique_parts.append(part)
        if len(unique_parts) == 1:
            return unique_parts[0]
        if len(unique_parts) >= 2 and len(set(part.lower() for part in unique_parts)) == 1:
            return unique_parts[0]
        cleaned = ", ".join(unique_parts)

    repeated_match = re.fullmatch(r"(.{3,40}?)(?:,\s*\1){2,}", cleaned, flags=re.IGNORECASE)
    if repeated_match:
        return repeated_match.group(1).strip(" ,.")
    return cleaned


def transcribe_audio(
    audio_path: Path,
    model_size: str = DEFAULT_MODEL_SIZE,
    language: str = DEFAULT_LANGUAGE,
    vad_filter: bool = True,
    initial_prompt: str | None = None,
) -> str:
    model = load_model(model_size)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=vad_filter,
        initial_prompt=initial_prompt or build_initial_prompt(),
    )

    print(f"Langue detectee: {info.language} ({info.language_probability:.2f})")
    text_parts: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {text}")
            text_parts.append(text)

    return " ".join(text_parts).strip()


def record_audio(output_path: Path, seconds: int, sample_rate: int = 16_000, device: int | None = None) -> None:
    import sounddevice as sd
    from scipy.io.wavfile import write

    print(f"Enregistrement pendant {seconds} secondes...")
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=device,
    )
    sd.wait()
    write(output_path, sample_rate, audio)
    print(f"Audio enregistre: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Speech-to-text local avec faster-whisper.")
    parser.add_argument(
        "--audio",
        type=Path,
        help="Chemin vers un fichier audio a transcrire: wav, mp3, m4a, etc.",
    )
    parser.add_argument(
        "--record",
        type=int,
        metavar="SECONDS",
        help="Enregistre le micro pendant N secondes puis transcrit.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_SIZE,
        help="Modele faster-whisper a utiliser. Recommande sur votre PC: base.",
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help="Langue forcee pour la transcription. Par defaut: fr.",
    )
    parser.add_argument(
        "--no-vad",
        action="store_true",
        help="Desactive le filtre de voix Whisper, utile si la voix est faible.",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="Index du micro a utiliser pour l'enregistrement.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Affiche les peripheriques audio disponibles puis quitte.",
    )
    parser.add_argument(
        "--link-db",
        action="store_true",
        help="Apres transcription, corrige les entites en les reliant aux donnees PostgreSQL.",
    )
    parser.add_argument(
        "--intent-hint",
        help="Indice d'intention pour aider la liaison: emploi_temps, etudiants, enseignants, etc.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    if not args.audio and not args.record:
        raise SystemExit("Utilisez --audio chemin_fichier ou --record SECONDS.")

    if args.audio:
        audio_path = args.audio
        if not audio_path.exists():
            raise FileNotFoundError(f"Fichier audio introuvable: {audio_path}")
        initial_prompt = None
        if args.link_db:
            with connect_db() as conn:
                initial_prompt = build_initial_prompt(conn)
        final_text = transcribe_audio(
            audio_path,
            model_size=args.model,
            language=args.language,
            vad_filter=not args.no_vad,
            initial_prompt=initial_prompt,
        )
    else:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            audio_path = Path(temp_file.name)
        record_audio(audio_path, seconds=args.record, device=args.device)
        initial_prompt = None
        if args.link_db:
            with connect_db() as conn:
                initial_prompt = build_initial_prompt(conn)
        final_text = transcribe_audio(
            audio_path,
            model_size=args.model,
            language=args.language,
            vad_filter=not args.no_vad,
            initial_prompt=initial_prompt,
        )

    final_text = clean_transcription_text(final_text)

    print("\nTexte final:")
    print(final_text or "[Aucun texte detecte]")

    if args.link_db and final_text:
        detected_route = parse_question(final_text)
        effective_intent = args.intent_hint or detected_route.intent
        with connect_db() as conn:
            linked = speech_linker_pipeline(final_text, conn, intent_hint=effective_intent)
        print("\nIntention detectee:")
        print(f"- intent={detected_route.intent}")
        print(f"- confidence={detected_route.confidence:.2f}")
        print(f"- entities_initiales={detected_route.entities}")
        print("\nInterpretation metier:")
        print(build_resolution_message(linked))
        print("\nPost-correction liee a la base:")
        print(json.dumps(linked, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
