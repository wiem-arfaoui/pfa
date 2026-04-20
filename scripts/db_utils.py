from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class FormationInfo:
    code: str
    nom: str


FORMATION_CANONICAL = {
    "informatique": FormationInfo(code="info", nom="Informatique"),
    "infotronique": FormationInfo(code="infotronique", nom="Infotronique"),
    "mecatronique": FormationInfo(code="mecatronique", nom="Mecatronique"),
    "industrielle": FormationInfo(code="industrielle", nom="Genie Industriel"),
}


FORMATION_ALIASES = {
    "info": "informatique",
    "informatique": "informatique",
    "genie informatique": "informatique",
    "infotronique": "infotronique",
    "gsi": "infotronique",
    "gsi-": "infotronique",
    "meca": "mecatronique",
    "m": "mecatronique",
    "mecatronique": "mecatronique",
    "indus": "industrielle",
    "industrielle": "industrielle",
    "genie industriel": "industrielle",
    "gi": "industrielle",
    "gsil": "industrielle",
}


MONTHS = {
    "jan": 1,
    "janv": 1,
    "janvier": 1,
    "fev": 2,
    "fevr": 2,
    "fevrier": 2,
    "fév": 2,
    "févr": 2,
    "février": 2,
    "mars": 3,
    "avr": 4,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "sept": 9,
    "septembre": 9,
    "oct": 10,
    "octobre": 10,
    "nov": 11,
    "novembre": 11,
    "dec": 12,
    "decembre": 12,
    "déc": 12,
    "décembre": 12,
}


def load_env_file(env_path: Path | None = None) -> dict[str, str]:
    path = env_path or (PROJECT_ROOT / ".env")
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
        os.environ.setdefault(key.strip(), value)
    return values


def find_data_root() -> Path:
    for candidate in (PROJECT_ROOT / "data", PROJECT_ROOT / "Data"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Impossible de trouver le dossier data/Data a la racine du projet.")


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return fix_mojibake(text)


def fix_mojibake(text: str) -> str:
    suspicious = ("Ã", "â€™", "â€“", "â€œ", "â€", "�")
    if any(token in text for token in suspicious):
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
    return text


def simplify(text: str) -> str:
    normalized = normalize_text(text).lower()
    normalized = normalized.replace("_", " ").replace("-", " ")
    normalized = normalized.replace("&", " & ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def normalize_formation_name(raw: str) -> str:
    simplified = simplify(raw)
    for alias, canonical in FORMATION_ALIASES.items():
        if simplified == alias or simplified.startswith(f"{alias} "):
            return canonical
    if "informatique" in simplified:
        return "informatique"
    if "infotronique" in simplified or "gsi" in simplified:
        return "infotronique"
    if "mecatronique" in simplified or re.fullmatch(r"m\s*\d.*", simplified):
        return "mecatronique"
    if "industriel" in simplified or "gsil" in simplified or "indus" in simplified:
        return "industrielle"
    raise ValueError(f"Formation inconnue: {raw}")


def formation_info(raw: str) -> FormationInfo:
    return FORMATION_CANONICAL[normalize_formation_name(raw)]


def infer_year_from_label(raw: str) -> int:
    cleaned = simplify(raw)
    match = re.search(r"\b([123])(ere|eme|e)?\b", cleaned)
    if match:
        return int(match.group(1))
    match = re.search(r"\b([123])\s*[abcd]\b", cleaned)
    if match:
        return int(match.group(1))
    match = re.search(r"\b([123])[abcd]\b", cleaned)
    if match:
        return int(match.group(1))
    raise ValueError(f"Impossible d'inferer l'annee depuis: {raw}")


def normalize_group_name(raw: str, formation: str, year_hint: int | None = None) -> str:
    text = normalize_text(raw)
    simple = simplify(text)
    year = year_hint
    if year is None:
        year = infer_year_from_label(text)

    if formation == "informatique":
        letter = _extract_group_letter(simple, "abcd")
        if not letter:
            raise ValueError(f"Groupe informatique non reconnu: {raw}")
        return f"INFO {year}{letter}"

    if formation == "infotronique":
        letter = _extract_group_letter(simple, "ab")
        if not letter:
            raise ValueError(f"Groupe infotronique non reconnu: {raw}")
        return f"GSI {year}{letter}"

    if formation == "mecatronique":
        letter = _extract_group_letter(simple, "abc")
        if not letter:
            raise ValueError(f"Groupe mecatronique non reconnu: {raw}")
        return f"MECA {year}{letter}"

    if formation == "industrielle":
        if year == 3:
            if "q&m" in simple or "q & m" in simple or "qualite" in simple or "qualité" in simple:
                return "GSIL 3 Q&M"
            if "msps" in simple or "production" in simple:
                return "GSIL 3 MSPS"
            if "log" in simple or "logistique" in simple:
                return "GSIL 3 LOG"
            raise ValueError(f"Groupe 3eme industrielle non reconnu: {raw}")
        letter = _extract_group_letter(simple, "abc")
        if not letter:
            raise ValueError(f"Groupe industrielle non reconnu: {raw}")
        return f"GSIL {year}{letter}"

    raise ValueError(f"Formation de groupe non geree: {formation}")


def _extract_group_letter(simple: str, allowed_letters: str) -> str | None:
    patterns = [
        rf"\bg([{allowed_letters}])\b",
        rf"\b([{allowed_letters}])\b",
        rf"\b[123]\s*([{allowed_letters}])\b",
        rf"\b(?:info|gsi|meca|gsil|m)\s*[123]\s*([{allowed_letters}])\b",
        rf"\b(?:info|gsi|meca|gsil|m)\s*[123][ -]*([{allowed_letters}])\b",
        rf"\b(?:ing)?[123]\s*(?:info|gsi|meca|gsil)\s*g([{allowed_letters}])\b",
        rf"\b(?:ing)?[123]\s*(?:info|gsi|meca|gsil)\s*([{allowed_letters}])\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, simple)
        if match:
            return match.group(1).upper()
    return None


def parse_decimal(value: object) -> float | None:
    text = normalize_text(value)
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def chunk_text(text: str, target_words: int = 350, overlap_words: int = 60) -> list[str]:
    words = normalize_text(text).split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + target_words)
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = max(start + target_words - overlap_words, start + 1)
    return chunks


def format_vector(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{value:.9f}" for value in values) + "]"


def infer_year_from_semester(semester: int) -> int:
    if semester in (1, 2):
        return 1
    if semester in (3, 4):
        return 2
    if semester in (5, 6):
        return 3
    raise ValueError(f"Semestre invalide: {semester}")


def semester_year_from_path(path: Path) -> tuple[int, int]:
    semester_match = re.search(r"semestre\s*([1-6])", simplify(path.stem))
    if not semester_match:
        raise ValueError(f"Semestre introuvable dans: {path}")
    semester = int(semester_match.group(1))
    year = infer_year_from_semester(semester)
    return year, semester


def parse_french_date_range(raw: str, academic_start_year: int) -> tuple[date | None, date | None]:
    text = simplify(raw)
    text = text.replace(".", "")

    patterns = [
        re.compile(r"^(?P<d1>\d{1,2})\s*-\s*(?P<d2>\d{1,2})\s*(?P<m1>[a-zéûôîà]+)$"),
        re.compile(r"^(?P<d1>\d{1,2})\s*(?P<m1>[a-zéûôîà]+)\s*-\s*(?P<d2>\d{1,2})\s*(?P<m2>[a-zéûôîà]+)$"),
        re.compile(r"^(?P<d1>\d{1,2})\s*-\s*(?P<m1>[a-zéûôîà]+)$"),
        re.compile(r"^(?P<d1>\d{1,2})\s*(?P<m1>[a-zéûôîà]+)$"),
    ]

    for pattern in patterns:
        match = pattern.match(text)
        if not match:
            continue

        d1 = int(match.group("d1"))
        m1_key = match.group("m1")
        m1 = MONTHS.get(m1_key)
        if m1 is None:
            return None, None
        y1 = academic_start_year if m1 >= 9 else academic_start_year + 1
        start = date(y1, m1, d1)

        d2_raw = match.groupdict().get("d2")
        if not d2_raw:
            return start, start

        d2 = int(d2_raw)
        m2_key = match.groupdict().get("m2") or m1_key
        m2 = MONTHS.get(m2_key)
        if m2 is None:
            return start, None
        y2 = academic_start_year if m2 >= 9 else academic_start_year + 1
        end = date(y2, m2, d2)
        return start, end

    return None, None
