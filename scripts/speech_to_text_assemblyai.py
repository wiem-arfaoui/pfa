from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from intent_router import parse_question
from speech_entity_linker import build_resolution_message, connect_db, speech_linker_pipeline
from stt_assemblyai_client import AssemblyAIError, DEFAULT_SPEECH_MODEL, KEYTERMS_PROMPT, transcribe_audio_file


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


def parse_keyterms(raw_value: str | None) -> list[str] | None:
    if raw_value is None:
        return KEYTERMS_PROMPT
    if not raw_value.strip():
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Speech-to-text via AssemblyAI.")
    parser.add_argument("--audio", type=Path, help="Chemin vers un fichier audio a transcrire.")
    parser.add_argument("--record", type=int, metavar="SECONDS", help="Enregistre le micro pendant N secondes.")
    parser.add_argument("--device", type=int, help="Index du micro a utiliser pour l'enregistrement.")
    parser.add_argument("--list-devices", action="store_true", help="Affiche les peripheriques audio.")
    parser.add_argument("--language-code", default="fr", help="Langue forcee. Defaut: fr.")
    parser.add_argument(
        "--speech-model",
        default=DEFAULT_SPEECH_MODEL,
        choices=("universal-2", "universal-3-pro"),
        help="Modele speech AssemblyAI. Defaut: universal-2.",
    )
    parser.add_argument(
        "--keyterms",
        default=None,
        help="Mots importants separes par virgule. Chaine vide pour desactiver.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Temps maximum d'attente de transcription en secondes.",
    )
    parser.add_argument(
        "--link-db",
        action="store_true",
        help="Apres transcription, relie les entites au projet via PostgreSQL.",
    )
    parser.add_argument(
        "--intent-hint",
        default=None,
        help="Optionnel: force une intention si vous voulez deboguer la resolution.",
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
    else:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            audio_path = Path(temp_file.name)
        record_audio(audio_path, seconds=args.record, device=args.device)

    try:
        final_text = transcribe_audio_file(
            audio_path,
            language_code=args.language_code,
            keyterms_prompt=parse_keyterms(args.keyterms),
            timeout_seconds=args.timeout,
            speech_model=args.speech_model,
        )
    except AssemblyAIError as exc:
        raise SystemExit(f"\nErreur AssemblyAI STT:\n{exc}") from exc

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
