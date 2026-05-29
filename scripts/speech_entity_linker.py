from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

import psycopg

from db_utils import load_env_file
from entity_resolver import SUBJECT_ALIASES
from intent_router import FORMATION_ALIASES, normalize_query


DAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
TIME_PERIOD_PATTERNS = {
    "morning": (r"\bmatin\b", r"\bmatinee\b", r"\bmatinnee\b"),
    "afternoon": (r"\bapres midi\b", r"\bapres midi\b", r"\bapres-midi\b", r"\bapresmidi\b"),
    "evening": (r"\bsoir\b", r"\bsoiree\b"),
}
YEAR_SPOKEN_VARIANTS = {
    "1": ("1", "un", "premiere", "premier", "one"),
    "2": ("2", "deux", "de", "to", "too"),
    "3": ("3", "trois", "three", "tri"),
}
GROUP_PREFIX_SPOKEN_VARIANTS = {
    "gsi": ("gsi", "g s i", "g es i", "g et i", "g et c", "g c", "jessi"),
    "gsil": ("gsil", "g s i l", "g essil", "g et c l", "jessil"),
    "info": ("info", "inf", "informatique"),
    "meca": ("meca", "méca", "m e c a", "meca tronique"),
}
LETTER_SPOKEN_VARIANTS = {
    "a": ("a",),
    "b": ("b", "be"),
    "c": ("c", "ce", "se"),
    "d": ("d", "de"),
}


@dataclass(frozen=True)
class CatalogItem:
    entity_type: str
    value: str
    normalized: str
    metadata: dict[str, Any]


STOPWORD_TOKENS = {
    "de", "du", "des", "la", "le", "les", "pour", "chez", "avec", "dans",
    "donnez", "donner", "emploi", "cours", "quel", "quelle", "quels", "quelles",
    "ou", "où", "quand", "est", "a", "se", "trouve", "etudie", "enseigne",
}
VOICE_FILLER_TOKENS = {"euh", "heu", "hm", "hmm", "eux"}
STUDENT_CONTEXT_MARKERS = (
    "emploi", "cours", "groupe", "salle", "etudiant", "etudiante", "etudie",
    "emploi du temps", "donnez l emploi", "quel cours", "quels cours",
)
TEACHER_CONTEXT_MARKERS = (
    "enseigne", "enseignant", "enseignante", "prof", "professeur",
)


def connect_db() -> psycopg.Connection[Any]:
    env = load_env_file()
    database_url = env.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL est introuvable dans .env")
    return psycopg.connect(database_url)


def normalize_speech_text(text: str) -> str:
    return normalize_query(text)


def compact_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_speech_text(text))


def remove_vowels(text: str) -> str:
    compacted = compact_text(text)
    without_vowels = re.sub(r"[aeiouy]", "", compacted)
    return without_vowels or compacted


def initials_signature(text: str) -> str:
    tokens = [token for token in normalize_speech_text(text).split() if token and token not in STOPWORD_TOKENS]
    if not tokens:
        return ""
    return "".join(token[0] for token in tokens)


def letters_only_signature(text: str) -> str:
    normalized = normalize_speech_text(text)
    if re.search(r"\b[a-z]\b", normalized):
        letters = [token for token in normalized.split() if re.fullmatch(r"[a-z]", token)]
        if letters:
            return "".join(letters)
    return compact_text(text)


def person_name_forms(value: str) -> dict[str, str]:
    normalized = normalize_speech_text(value)
    return {
        "normalized": normalized,
        "compact": compact_text(value),
        "phonetic": phonetic_name_compact(normalized),
        "initials": initials_signature(value),
        "consonants": remove_vowels(value),
        "letters_only": letters_only_signature(value),
        "first_token": normalized.split()[0] if normalized.split() else "",
        "last_token": normalized.split()[-1] if normalized.split() else "",
    }


def load_entity_catalog(conn: psycopg.Connection[Any]) -> dict[str, list[CatalogItem]]:
    catalog: dict[str, list[CatalogItem]] = {
        "students": [],
        "groups": [],
        "formations": [],
        "subjects": [],
        "teachers": [],
        "rooms": [],
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.nom_complet, e.email, g.nom_groupe
            FROM etudiants e
            JOIN groupes g ON g.id = e.groupe_id
            ORDER BY e.nom_complet
            """
        )
        for name, email, group_name in cur.fetchall():
            forms = person_name_forms(str(name))
            email_local = ""
            if email:
                email_local = normalize_speech_text(str(email).split("@", 1)[0].replace(".", " "))
            catalog["students"].append(
                CatalogItem(
                    entity_type="student",
                    value=str(name),
                    normalized=normalize_speech_text(str(name)),
                    metadata={
                        "email": str(email) if email else None,
                        "group": str(group_name),
                        "name_forms": forms,
                        "email_local": email_local,
                    },
                )
            )

        cur.execute(
            """
            SELECT g.nom_groupe, g.annee, f.code, f.nom
            FROM groupes g
            JOIN formations f ON f.id = g.formation_id
            ORDER BY g.nom_groupe
            """
        )
        for group_name, year, code, formation_name in cur.fetchall():
            catalog["groups"].append(
                CatalogItem(
                    entity_type="group",
                    value=str(group_name),
                    normalized=normalize_speech_text(str(group_name)),
                    metadata={
                        "year": int(year),
                        "formation_code": str(code),
                        "formation": normalize_speech_text(str(formation_name)),
                    },
                )
            )

        cur.execute("SELECT code, nom FROM formations ORDER BY nom")
        for code, name in cur.fetchall():
            catalog["formations"].append(
                CatalogItem(
                    entity_type="formation",
                    value=str(name),
                    normalized=normalize_speech_text(str(name)),
                    metadata={"formation_code": str(code)},
                )
            )

        cur.execute(
            """
            SELECT nom FROM matieres
            UNION
            SELECT matiere FROM emplois_temps
            ORDER BY 1
            """
        )
        for (subject,) in cur.fetchall():
            if not subject:
                continue
            catalog["subjects"].append(
                CatalogItem(
                    entity_type="subject",
                    value=str(subject),
                    normalized=normalize_speech_text(str(subject)),
                    metadata={},
                )
            )

        cur.execute(
            """
            SELECT DISTINCT enseignant
            FROM emplois_temps
            WHERE enseignant IS NOT NULL AND TRIM(enseignant) <> ''
            ORDER BY enseignant
            """
        )
        for (teacher_raw,) in cur.fetchall():
            for teacher in split_slash_parts(str(teacher_raw)):
                forms = person_name_forms(teacher)
                catalog["teachers"].append(
                    CatalogItem(
                        entity_type="teacher",
                        value=teacher,
                        normalized=normalize_speech_text(teacher),
                        metadata={"name_forms": forms},
                    )
                )

        cur.execute(
            """
            SELECT DISTINCT salle
            FROM emplois_temps
            WHERE salle IS NOT NULL AND TRIM(salle) <> ''
            ORDER BY salle
            """
        )
        for (room_raw,) in cur.fetchall():
            for room in split_slash_parts(str(room_raw)):
                catalog["rooms"].append(
                    CatalogItem(
                        entity_type="room",
                        value=room,
                        normalized=normalize_speech_text(room),
                        metadata={},
                    )
                )

    return catalog


def split_slash_parts(value: str) -> list[str]:
    return [part.strip() for part in value.split("/") if part.strip()]


def build_dynamic_aliases(catalog: dict[str, list[CatalogItem]]) -> dict[str, dict[str, str]]:
    aliases: dict[str, dict[str, str]] = {
        "formations": {},
        "subjects": {},
        "groups": {},
    }

    for canonical, variants in FORMATION_ALIASES.items():
        for variant in variants:
            normalized_variant = normalize_speech_text(variant)
            if len(normalized_variant) <= 1:
                continue
            aliases["formations"][normalized_variant] = canonical

    for canonical, variants in SUBJECT_ALIASES.items():
        aliases["subjects"][normalize_speech_text(canonical)] = canonical
        for variant in variants:
            aliases["subjects"][normalize_speech_text(variant)] = canonical

    for item in catalog["groups"]:
        group = item.value
        normalized = item.normalized
        aliases["groups"][normalized] = group
        compact = normalized.replace(" ", "")
        aliases["groups"][compact] = group

        match = re.match(r"^(info|gsi|meca|gsil)\s+([123])([a-z])$", normalized)
        if match:
            prefix, year, letter = match.groups()
            prefixes = {prefix}
            if prefix == "info":
                prefixes.update({"inf", "informatique"})
            elif prefix == "gsi":
                prefixes.update({"infotronique"})
            elif prefix == "meca":
                prefixes.update({"m", "mecatronique"})
            elif prefix == "gsil":
                prefixes.update({"indus", "industrielle"})
            for variant_prefix in prefixes:
                for variant in (
                    f"{variant_prefix} {year}{letter}",
                    f"{variant_prefix}{year}{letter}",
                    f"{variant_prefix} {year} {letter}",
                ):
                    aliases["groups"][normalize_speech_text(variant)] = group

        if normalized.startswith("gsil 3 "):
            specialty = normalized.replace("gsil 3 ", "", 1).strip()
            for variant in (specialty, f"gsil {specialty}", f"indus {specialty}", f"industrielle {specialty}"):
                aliases["groups"][normalize_speech_text(variant)] = group
        for variant in group_speech_variants(group):
            aliases["groups"][normalize_speech_text(variant)] = group

    return aliases


def extract_candidate_spans(text: str) -> dict[str, list[str]]:
    normalized = normalize_speech_text(text)
    spans: dict[str, list[str]] = {
        "person_candidates": [],
        "subject_candidates": [],
        "group_candidates": [],
        "formation_candidates": [],
        "room_candidates": [],
    }

    group_patterns = [
        r"\b(?:gsi|info|inf|meca|m|gsil)\s*[123]\s*[a-z]\b",
        r"\b(?:gsi|info|inf|meca|m|gsil)\s*[123][a-z]\b",
        r"\b(?:gsil\s*3\s*(?:log|msps|q\s*&\s*m|q&m|qualite|production|logistique))\b",
    ]
    for pattern in group_patterns:
        spans["group_candidates"].extend(match.group(0).strip() for match in re.finditer(pattern, normalized))

    room_patterns = [
        r"\b(?:salle\s+)?[ls]\d{1,3}\b",
        r"\bl\d{3}\b",
        r"\bs\d{1,2}\b",
    ]
    for pattern in room_patterns:
        spans["room_candidates"].extend(match.group(0).replace("salle ", "").strip() for match in re.finditer(pattern, normalized))

    for canonical, variants in FORMATION_ALIASES.items():
        for variant in variants:
            normalized_variant = normalize_speech_text(variant)
            if len(normalized_variant) <= 1:
                continue
            if re.search(rf"\b{re.escape(normalized_variant)}\b", normalized):
                spans["formation_candidates"].append(canonical)
                break

    person_patterns = [
        r"(?:de|d|pour|chez|avec)\s+([a-z]{2,}\s+[a-z]{2,}(?:\s+[a-z]{2,})?)",
        r"(?:quand|ou|quel|quelle|quels|quelles)\s+(?:est|a|se trouve|etudie|enseigne)?\s*([a-z]{2,}\s+[a-z]{2,}(?:\s+[a-z]{2,})?)",
        r"(?:de|du|pour)\s+([a-z](?:\s*[a-z]){2,15})",
        r"\b([a-z]{2,}(?:-[a-z]{2,})+)\b",
    ]
    for pattern in person_patterns:
        for match in re.finditer(pattern, normalized):
            candidate = match.group(1).strip()
            if candidate and candidate not in spans["person_candidates"]:
                spans["person_candidates"].append(candidate)

    subject_patterns = [
        r"(?:cours de|matiere de|module de|etudie|enseigne)\s+([a-z0-9&. ]{2,60}?)(?=\s+(?:pour|du|de la|de l|a|au|aux|dans|le|la)\b|$)",
        r"(?:liste des matieres de)\s+([a-z0-9&. ]{2,60})",
    ]
    for pattern in subject_patterns:
        for match in re.finditer(pattern, normalized):
            candidate = match.group(1).strip()
            if candidate and candidate not in spans["subject_candidates"]:
                spans["subject_candidates"].append(candidate)

    for key in spans:
        spans[key] = dedupe_preserve_order(spans[key])

    spans["person_candidates"] = dedupe_preserve_order(
        spans["person_candidates"] + extract_spelled_letter_candidates(normalized)
    )
    return spans


def extract_spelled_letter_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    hyphen_chunks = re.findall(r"\b[a-z]{1,4}(?:-[a-z]{1,4})+\b", text)
    candidates.extend(hyphen_chunks)

    letter_runs = re.findall(r"\b(?:[a-z]\s+){2,15}[a-z]\b", text)
    candidates.extend(letter_runs)

    return dedupe_preserve_order(candidates)


def is_spelled_letters_candidate(text: str) -> bool:
    normalized = normalize_speech_text(text)
    if re.fullmatch(r"(?:[a-z]\s+){2,15}[a-z]", normalized):
        return True
    if re.fullmatch(r"[a-z]{1,4}(?:-[a-z]{1,4})+", normalized):
        return True
    return False


def infer_person_role_from_context(text: str, intent_hint: str | None = None) -> str:
    normalized = normalize_speech_text(text)
    if intent_hint == "enseignants" or any(marker in normalized for marker in TEACHER_CONTEXT_MARKERS):
        return "teacher"
    if intent_hint in {"emploi_temps", "etudiants", "salles", "groupes"} or any(
        marker in normalized for marker in STUDENT_CONTEXT_MARKERS
    ):
        return "student"
    return "unknown"


def extract_contextual_person_candidates(text: str) -> list[str]:
    normalized = normalize_speech_text(text)
    patterns = [
        r"(?:emploi(?: du temps)?\s+d(?:e)?|cours\s+d(?:e)?|groupe\s+d(?:e)?|salle\s+d(?:e)?|pour)\s+(.+)$",
        r"(?:quand enseigne|ou enseigne|où enseigne)\s+(.+)$",
        r"(?:donnez l emploi\s+d(?:e)?|donner l emploi\s+d(?:e)?)\s+(.+)$",
    ]
    candidates: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        candidate = match.group(1).strip()
        candidate = re.split(r"\b(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|aujourd hui|demain|hier)\b", candidate)[0].strip()
        if candidate:
            candidates.extend(generate_person_candidate_variants(candidate))
    return dedupe_preserve_order(candidates)


def generate_person_candidate_variants(candidate: str) -> list[str]:
    normalized = normalize_speech_text(candidate)
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return []

    cleaned_tokens = [token for token in tokens if token not in VOICE_FILLER_TOKENS]
    if not cleaned_tokens:
        cleaned_tokens = tokens

    variants = {
        " ".join(cleaned_tokens),
        "".join(cleaned_tokens),
    }

    if len(cleaned_tokens) >= 2:
        variants.add(" ".join(cleaned_tokens[-2:]))
        variants.add("".join(cleaned_tokens[-2:]))
    if len(cleaned_tokens) >= 3:
        variants.add(" ".join(cleaned_tokens[-3:]))
        variants.add("".join(cleaned_tokens[-3:]))

    merged = merge_short_tokens(cleaned_tokens)
    variants.update(merged)

    phonetic_variants: set[str] = set()
    for variant in list(variants):
        phonetic = phonetic_name_compact(variant)
        if phonetic:
            phonetic_variants.add(phonetic)
    variants.update(phonetic_variants)

    return [variant for variant in variants if variant]


def merge_short_tokens(tokens: list[str]) -> set[str]:
    if not tokens:
        return set()
    merged_variants = {" ".join(tokens)}
    working = tokens[:]

    index = 0
    while index < len(working):
        token = working[index]
        if len(token) == 1 and index > 0:
            working[index - 1] = working[index - 1] + token
            del working[index]
            continue
        index += 1

    merged_variants.add(" ".join(working))
    merged_variants.add("".join(working))

    if len(working) >= 2:
        merged_variants.add(f"{working[0]} {''.join(working[1:])}")
        merged_variants.add(f"{''.join(working[:-1])} {working[-1]}")

    return {variant.strip() for variant in merged_variants if variant.strip()}


def group_speech_variants(group_name: str) -> list[str]:
    normalized = normalize_speech_text(group_name)
    variants = {normalized, normalized.replace(" ", "")}

    specialty_match = re.match(r"^(gsil)\s+(3)\s+(log|msps|q&m)$", normalized)
    if specialty_match:
        prefix, year, specialty = specialty_match.groups()
        specialty_variants = {
            "log": ("log", "logistique"),
            "msps": ("msps", "production"),
            "q&m": ("q et m", "q&m", "qualite"),
        }
        for prefix_variant in GROUP_PREFIX_SPOKEN_VARIANTS.get(prefix, (prefix,)):
            for year_variant in YEAR_SPOKEN_VARIANTS.get(year, (year,)):
                for specialty_variant in specialty_variants.get(specialty, (specialty,)):
                    variants.add(f"{prefix_variant} {year_variant} {specialty_variant}")
        return sorted(variants)

    match = re.match(r"^(info|gsi|meca|gsil)\s+([123])([a-z])$", normalized)
    if not match:
        return sorted(variants)

    prefix, year, letter = match.groups()
    prefix_variants = GROUP_PREFIX_SPOKEN_VARIANTS.get(prefix, (prefix,))
    year_variants = YEAR_SPOKEN_VARIANTS.get(year, (year,))
    letter_variants = LETTER_SPOKEN_VARIANTS.get(letter, (letter,))

    for prefix_variant in prefix_variants:
        for year_variant in year_variants:
            for letter_variant in letter_variants:
                variants.add(f"{prefix_variant} {year}{letter_variant}")
                variants.add(f"{prefix_variant} {year_variant}{letter_variant}")
                variants.add(f"{prefix_variant} {year_variant} {letter_variant}")

    if prefix == "gsi" and year == "2":
        # Cas frequent vu en STT: "GSI 2B" -> "G et C de B"
        for letter_variant in letter_variants:
            variants.add(f"g et c de {letter_variant}")
            variants.add(f"g et c {year} {letter_variant}")
            variants.add(f"g c de {letter_variant}")

    return sorted(variants)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def extract_alias_candidates(text: str, alias_map: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for alias in sorted(alias_map.keys(), key=len, reverse=True):
        if not alias:
            continue
        if " " in alias:
            if re.search(rf"\b{re.escape(alias)}\b", text):
                candidates.append(alias)
        elif re.search(rf"\b{re.escape(alias)}\b", text):
            candidates.append(alias)
    return dedupe_preserve_order(candidates)


def filter_person_candidates(
    candidates: list[str],
    group_alias_map: dict[str, str],
    formation_alias_map: dict[str, str],
) -> list[str]:
    filtered: list[str] = []
    for candidate in candidates:
        normalized = normalize_speech_text(candidate).strip(". ")
        if not normalized:
            continue
        if normalized in group_alias_map:
            continue
        if normalized in formation_alias_map:
            continue
        if any(token in normalized.split() for token in ("emploi", "emplois", "gsi", "gsil", "info", "meca", "inf")):
            continue
        if re.fullmatch(r"(gsi|info|inf|meca|gsil)\s*[123]?[a-z]?", normalized):
            continue
        filtered.append(candidate)
    return dedupe_preserve_order(filtered)


def should_skip_person_resolution(
    normalized_text: str,
    candidate_spans: dict[str, list[str]],
    inferred_person_role: str,
) -> bool:
    if inferred_person_role != "student":
        return False
    if candidate_spans.get("group_candidates"):
        return True
    if candidate_spans.get("formation_candidates") and not candidate_spans.get("person_candidates"):
        return True
    if re.search(r"\b(gsi|gsil|info|meca|inf)\b", normalized_text) and not candidate_spans.get("person_candidates"):
        return True
    return False


def resolve_days_and_time_refs(text: str) -> dict[str, str]:
    normalized = normalize_speech_text(text)
    result: dict[str, str] = {}
    today = date.today()

    if re.search(r"\b(aujourd hui|aujourdhui|today)\b", normalized):
        result["day"] = DAY_NAMES[today.weekday()]
    elif re.search(r"\b(demain|tomorrow)\b", normalized):
        result["day"] = DAY_NAMES[(today + timedelta(days=1)).weekday()]
    elif re.search(r"\b(hier|yesterday)\b", normalized):
        result["day"] = DAY_NAMES[(today - timedelta(days=1)).weekday()]
    else:
        for day_name in DAY_NAMES:
            if re.search(rf"\b{re.escape(day_name)}\b", normalized):
                result["day"] = day_name
                break

    for period, patterns in TIME_PERIOD_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            result["time_period"] = period
            break

    return result


def similarity_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    ratio = SequenceMatcher(None, left, right).ratio()
    prefix_bonus = 0.08 if (left.startswith(right) or right.startswith(left)) else 0.0
    return min(1.0, (ratio * 0.7) + (overlap * 0.3) + prefix_bonus)


def phonetic_name_compact(text: str) -> str:
    normalized = normalize_speech_text(text)
    replacements = (
        ("ouim", "wim"),
        ("oui", "wi"),
        ("oua", "wa"),
        ("ou", "u"),
        ("ph", "f"),
        ("ck", "k"),
        ("qu", "k"),
        ("q", "k"),
        ("x", "ks"),
        ("y", "i"),
        ("ai", "e"),
        ("ei", "e"),
        ("ait", "e"),
        ("ais", "e"),
        ("er", "e"),
        ("eau", "o"),
        ("au", "o"),
        ("h", ""),
    )
    phonetic = normalized
    for source, target in replacements:
        phonetic = phonetic.replace(source, target)
    phonetic = compact_text(phonetic)
    phonetic = re.sub(r"(.)\1+", r"\1", phonetic)
    return phonetic


def subsequence_score(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if needle == haystack:
        return 1.0
    index = 0
    matched = 0
    for char in haystack:
        if index < len(needle) and char == needle[index]:
            matched += 1
            index += 1
    if matched == 0:
        return 0.0
    return matched / max(1, len(needle))


def person_name_score(candidate: str, item: CatalogItem) -> float:
    forms = item.metadata.get("name_forms", {})
    candidate_normalized = normalize_speech_text(candidate)
    candidate_compact = compact_text(candidate)
    candidate_phonetic = phonetic_name_compact(candidate)
    candidate_consonants = remove_vowels(candidate)
    candidate_initials = initials_signature(candidate)
    candidate_letters = letters_only_signature(candidate)

    scores = [
        similarity_score(candidate_normalized, item.normalized),
        similarity_score(candidate_compact, forms.get("compact", "")),
        similarity_score(candidate_phonetic, forms.get("phonetic", "")),
        similarity_score(candidate_consonants, forms.get("consonants", "")),
        similarity_score(candidate_letters, forms.get("letters_only", "")),
        subsequence_score(candidate_consonants, forms.get("consonants", "")),
        subsequence_score(forms.get("consonants", ""), candidate_letters),
    ]
    email_local = item.metadata.get("email_local", "")
    if email_local:
        scores.append(similarity_score(candidate_normalized, email_local))
        scores.append(similarity_score(candidate_compact, compact_text(email_local)))

    item_initials = forms.get("initials", "")
    if candidate_initials and item_initials:
        scores.append(similarity_score(candidate_initials, item_initials))
    if candidate_letters and item_initials:
        scores.append(similarity_score(candidate_letters, item_initials))

    # Boost utile pour des formes très compressées du type WM-RF
    if candidate_compact and forms.get("consonants") and candidate_compact == forms["consonants"][:len(candidate_compact)]:
        scores.append(0.92)
    if candidate_letters and forms.get("consonants") and candidate_letters == forms["consonants"][:len(candidate_letters)]:
        scores.append(0.9)
    if candidate_letters and forms.get("consonants"):
        subseq = subsequence_score(forms["consonants"], candidate_letters)
        if subseq >= 0.7:
            scores.append(0.78 + (subseq * 0.12))
    if candidate_normalized and forms.get("last_token") and similarity_score(candidate_normalized, forms["last_token"]) >= 0.82:
        scores.append(0.88)
    if candidate_normalized and forms.get("first_token") and similarity_score(candidate_normalized, forms["first_token"]) >= 0.82:
        scores.append(0.84)
    if candidate_phonetic and forms.get("phonetic"):
        if candidate_phonetic == forms["phonetic"]:
            scores.append(1.0)
        else:
            scores.append(subsequence_score(candidate_phonetic, forms["phonetic"]))

    return max(scores)


def phonetic_group_score(candidate: str, group_value: str) -> float:
    normalized_candidate = normalize_speech_text(candidate)
    normalized_group = normalize_speech_text(group_value)
    variants = [normalize_speech_text(variant) for variant in group_speech_variants(group_value)]
    best = max(similarity_score(normalized_candidate, variant) for variant in variants)
    if normalized_candidate.replace(" ", "") == normalized_group.replace(" ", ""):
        return 1.0
    return best


def match_candidates(
    candidates: list[str],
    catalog_items: list[CatalogItem],
    threshold: float,
    alias_map: dict[str, str] | None = None,
    entity_type: str | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        normalized_candidate = normalize_speech_text(candidate)
        if not normalized_candidate:
            continue

        if alias_map and normalized_candidate in alias_map:
            canonical = alias_map[normalized_candidate]
            for item in catalog_items:
                if item.normalized == normalize_speech_text(canonical) or item.value == canonical:
                    matches.append(
                        {
                            "input": candidate,
                            "matched_value": item.value,
                            "score": 1.0,
                            "metadata": item.metadata,
                        }
                    )
                    break
            continue

        best_item: CatalogItem | None = None
        best_score = 0.0
        for item in catalog_items:
            if entity_type == "group":
                score = phonetic_group_score(normalized_candidate, item.value)
            elif entity_type in {"student", "teacher"}:
                score = person_name_score(normalized_candidate, item)
            else:
                score = similarity_score(normalized_candidate, item.normalized)
            if score > best_score:
                best_score = score
                best_item = item

        if best_item and best_score >= threshold:
            matches.append(
                {
                    "input": candidate,
                    "matched_value": best_item.value,
                    "score": round(best_score, 4),
                    "metadata": best_item.metadata,
                }
            )
    return matches


def choose_best_entity(matches: list[dict[str, Any]], intent_hint: str | None = None) -> dict[str, Any] | None:
    if not matches:
        return None
    sorted_matches = sorted(matches, key=lambda item: item["score"], reverse=True)
    best = sorted_matches[0]
    if len(sorted_matches) == 1:
        return best

    second = sorted_matches[1]
    if best["matched_value"] == second["matched_value"]:
        return best

    if best["score"] - second["score"] >= 0.08:
        return best

    if intent_hint == "enseignants":
        teacher_like = [item for item in sorted_matches if item["metadata"] == {}]
        if teacher_like:
            return teacher_like[0]
    if intent_hint in {"emploi_temps", "etudiants", "salles", "groupes"} and len(sorted_matches) > 1:
        if best["score"] >= 0.68 and (best["score"] - second["score"]) >= 0.12:
            return best
    return best


def top_matches_for_people(
    candidates: list[str],
    catalog_items: list[CatalogItem],
    threshold: float,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        normalized_candidate = normalize_speech_text(candidate)
        if not normalized_candidate:
            continue
        for item in catalog_items:
            score = person_name_score(normalized_candidate, item)
            if score >= threshold:
                ranked.append(
                    {
                        "input": candidate,
                        "matched_value": item.value,
                        "score": round(score, 4),
                        "metadata": item.metadata,
                    }
                )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        if item["matched_value"] in seen:
            continue
        seen.add(item["matched_value"])
        unique.append(item)
        if len(unique) >= 3:
            break
    return unique


def inject_resolved_entities(text: str, resolved_entities: dict[str, Any]) -> str:
    corrected = text
    replacements = []
    if resolved_entities.get("student_name"):
        replacements.append((resolved_entities["student_name"], "student_name"))
    if resolved_entities.get("teacher_name"):
        replacements.append((resolved_entities["teacher_name"], "teacher_name"))
    if resolved_entities.get("group"):
        replacements.append((resolved_entities["group"], "group"))
    if resolved_entities.get("formation"):
        replacements.append((resolved_entities["formation"], "formation"))
    if resolved_entities.get("subject"):
        replacements.append((resolved_entities["subject"], "subject"))
    if resolved_entities.get("room"):
        replacements.append((resolved_entities["room"], "room"))

    normalized_corrected = normalize_speech_text(corrected)
    for canonical_value, _entity_key in replacements:
        normalized_canonical = normalize_speech_text(canonical_value)
        if normalized_canonical in normalized_corrected:
            continue
        corrected = f"{corrected} [{canonical_value}]"
        normalized_corrected = normalize_speech_text(corrected)
    return corrected


def resolve_entities_from_speech(
    text: str,
    conn: psycopg.Connection[Any],
    intent_hint: str | None = None,
) -> dict[str, Any]:
    normalized_text = normalize_speech_text(text)
    catalog = load_entity_catalog(conn)
    aliases = build_dynamic_aliases(catalog)
    candidate_spans = extract_candidate_spans(normalized_text)
    contextual_person_candidates = extract_contextual_person_candidates(normalized_text)
    candidate_spans["person_candidates"] = dedupe_preserve_order(
        contextual_person_candidates + candidate_spans["person_candidates"]
    )
    candidate_spans["group_candidates"] = dedupe_preserve_order(
        candidate_spans["group_candidates"] + extract_alias_candidates(normalized_text, aliases["groups"])
    )
    candidate_spans["formation_candidates"] = dedupe_preserve_order(
        candidate_spans["formation_candidates"] + extract_alias_candidates(normalized_text, aliases["formations"])
    )
    candidate_spans["subject_candidates"] = dedupe_preserve_order(
        candidate_spans["subject_candidates"] + extract_alias_candidates(normalized_text, aliases["subjects"])
    )
    candidate_spans["person_candidates"] = filter_person_candidates(
        candidate_spans["person_candidates"],
        aliases["groups"],
        aliases["formations"],
    )
    temporal_entities = resolve_days_and_time_refs(normalized_text)

    inferred_person_role = infer_person_role_from_context(normalized_text, intent_hint=intent_hint)
    skip_person_resolution = should_skip_person_resolution(normalized_text, candidate_spans, inferred_person_role)

    student_matches = (
        match_candidates(
            candidate_spans["person_candidates"],
            catalog["students"],
            threshold=0.62,
            entity_type="student",
        )
        if inferred_person_role in {"student", "unknown"} and not skip_person_resolution
        else []
    )
    teacher_matches = (
        match_candidates(
            candidate_spans["person_candidates"],
            catalog["teachers"],
            threshold=0.66,
            entity_type="teacher",
        )
        if inferred_person_role in {"teacher", "unknown"}
        else []
    )
    group_matches = match_candidates(
        candidate_spans["group_candidates"],
        catalog["groups"],
        threshold=0.72,
        alias_map=aliases["groups"],
        entity_type="group",
    )
    formation_matches = match_candidates(candidate_spans["formation_candidates"], catalog["formations"], threshold=0.9, alias_map=aliases["formations"])
    subject_matches = match_candidates(candidate_spans["subject_candidates"], catalog["subjects"], threshold=0.68, alias_map=aliases["subjects"])
    room_matches = match_candidates(candidate_spans["room_candidates"], catalog["rooms"], threshold=0.88)

    best_student = choose_best_entity(student_matches, intent_hint=intent_hint)
    best_teacher = choose_best_entity(teacher_matches, intent_hint=intent_hint)
    best_group = choose_best_entity(group_matches, intent_hint=intent_hint)
    best_formation = choose_best_entity(formation_matches, intent_hint=intent_hint)
    best_subject = choose_best_entity(subject_matches, intent_hint=intent_hint)
    best_room = choose_best_entity(room_matches, intent_hint=intent_hint)

    resolved_entities: dict[str, Any] = {}
    confidence: dict[str, float] = {}
    top_candidates: dict[str, list[dict[str, Any]]] = {}

    if best_student and candidate_spans["person_candidates"]:
        primary_person_candidate = candidate_spans["person_candidates"][0]
        if is_spelled_letters_candidate(primary_person_candidate) and best_student["score"] < 0.9:
            best_student = None
            top_candidates["students"] = top_matches_for_people(
                candidate_spans["person_candidates"],
                catalog["students"],
                threshold=0.55,
            )

    if not best_student and inferred_person_role in {"student", "unknown"} and candidate_spans["person_candidates"] and not skip_person_resolution:
        top_candidates.setdefault(
            "students",
            top_matches_for_people(candidate_spans["person_candidates"], catalog["students"], threshold=0.55),
        )

    if not best_teacher and inferred_person_role in {"teacher", "unknown"} and candidate_spans["person_candidates"]:
        top_candidates.setdefault(
            "teachers",
            top_matches_for_people(candidate_spans["person_candidates"], catalog["teachers"], threshold=0.58),
        )

    if best_student:
        resolved_entities["student_name"] = best_student["matched_value"]
        confidence["student_name"] = best_student["score"]
        metadata = best_student["metadata"]
        if metadata.get("email"):
            resolved_entities["student_email"] = metadata["email"]
            confidence["student_email"] = 1.0
        if metadata.get("group"):
            resolved_entities["group"] = metadata["group"]
            confidence["group"] = 1.0

    if best_teacher and "teacher_name" not in resolved_entities:
        resolved_entities["teacher_name"] = best_teacher["matched_value"]
        confidence["teacher_name"] = best_teacher["score"]

    if best_group and "group" not in resolved_entities:
        resolved_entities["group"] = best_group["matched_value"]
        confidence["group"] = best_group["score"]
        metadata = best_group["metadata"]
        if metadata.get("year") and "year" not in resolved_entities:
            resolved_entities["year"] = metadata["year"]
            confidence["year"] = 1.0
        if metadata.get("formation") and "formation" not in resolved_entities:
            resolved_entities["formation"] = metadata["formation"]
            confidence["formation"] = 1.0
        if metadata.get("formation_code") and "formation_code" not in resolved_entities:
            resolved_entities["formation_code"] = metadata["formation_code"]
            confidence["formation_code"] = 1.0

    if best_formation and "formation" not in resolved_entities:
        resolved_entities["formation"] = best_formation["matched_value"]
        confidence["formation"] = best_formation["score"]
        metadata = best_formation["metadata"]
        if metadata.get("formation_code"):
            resolved_entities["formation_code"] = metadata["formation_code"]
            confidence["formation_code"] = 1.0

    if best_subject:
        resolved_entities["subject"] = best_subject["matched_value"]
        confidence["subject"] = best_subject["score"]

    if best_room:
        resolved_entities["room"] = best_room["matched_value"]
        confidence["room"] = best_room["score"]

    resolved_entities.update(temporal_entities)
    for key, value in temporal_entities.items():
        if value:
            confidence[key] = 1.0

    corrected_text = inject_resolved_entities(text, resolved_entities)

    ambiguous: list[dict[str, Any]] = []
    if len(student_matches) > 1 and student_matches[0]["score"] - student_matches[1]["score"] < 0.08:
        ambiguous.append({"type": "student", "matches": student_matches[:3]})
    if len(teacher_matches) > 1 and teacher_matches[0]["score"] - teacher_matches[1]["score"] < 0.08:
        ambiguous.append({"type": "teacher", "matches": teacher_matches[:3]})

    unresolved: list[str] = []
    if candidate_spans["person_candidates"] and not (best_student or best_teacher):
        unresolved.append("person")
    if candidate_spans["subject_candidates"] and not best_subject:
        unresolved.append("subject")
    if candidate_spans["group_candidates"] and not best_group:
        unresolved.append("group")

    return {
        "raw_text": text,
        "normalized_text": normalized_text,
        "candidate_spans": candidate_spans,
        "resolved_entities": resolved_entities,
        "confidence": confidence,
        "corrected_text": corrected_text,
        "ambiguous": ambiguous,
        "unresolved": unresolved,
        "top_candidates": top_candidates,
        "inferred_person_role": inferred_person_role,
    }


def speech_linker_pipeline(text: str, conn: psycopg.Connection[Any], intent_hint: str | None = None) -> dict[str, Any]:
    return resolve_entities_from_speech(text, conn, intent_hint=intent_hint)


def build_resolution_message(result: dict[str, Any]) -> str:
    resolved = result.get("resolved_entities", {})
    top_candidates = result.get("top_candidates", {})
    inferred_role = result.get("inferred_person_role", "unknown")

    if resolved.get("student_name"):
        group = resolved.get("group")
        if group:
            return f"Nom etudiant resolu: {resolved['student_name']} ({group})."
        return f"Nom etudiant resolu: {resolved['student_name']}."

    if resolved.get("teacher_name"):
        return f"Nom enseignant resolu: {resolved['teacher_name']}."

    if resolved.get("group"):
        return f"Groupe resolu: {resolved['group']}."

    suggestions: list[str] = []
    if inferred_role == "student" and top_candidates.get("students"):
        suggestions = [
            format_candidate_line(candidate, role="etudiant")
            for candidate in top_candidates["students"]
        ]
        title = "Nom etudiant incertain. Voulez-vous dire :"
    elif inferred_role == "teacher" and top_candidates.get("teachers"):
        suggestions = [
            format_candidate_line(candidate, role="enseignant")
            for candidate in top_candidates["teachers"]
        ]
        title = "Nom enseignant incertain. Voulez-vous dire :"
    elif top_candidates.get("students"):
        suggestions = [
            format_candidate_line(candidate, role="etudiant")
            for candidate in top_candidates["students"]
        ]
        title = "Nom incertain. Meilleurs candidats etudiants :"
    elif top_candidates.get("teachers"):
        suggestions = [
            format_candidate_line(candidate, role="enseignant")
            for candidate in top_candidates["teachers"]
        ]
        title = "Nom incertain. Meilleurs candidats enseignants :"
    else:
        return "Aucun nom propre n'a ete reconnu avec suffisamment de confiance."

    return title + "\n- " + "\n- ".join(suggestions)


def format_candidate_line(candidate: dict[str, Any], role: str) -> str:
    name = candidate["matched_value"]
    score = candidate["score"]
    metadata = candidate.get("metadata", {})
    if role == "etudiant":
        group = metadata.get("group")
        if group:
            return f"{name} ({group}, score {score:.2f})"
    return f"{name} (score {score:.2f})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Liaison post-transcription -> entites metier de la base.")
    parser.add_argument("--text", required=True, help="Texte brut issu du speech-to-text a corriger.")
    parser.add_argument("--intent-hint", help="Indice d'intention: emploi_temps, etudiants, enseignants, etc.")
    parser.add_argument("--pretty", action="store_true", help="Affiche le resultat JSON de facon lisible.")
    parser.add_argument("--message", action="store_true", help="Affiche aussi un message utilisateur interpretable.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    with connect_db() as conn:
        result = speech_linker_pipeline(args.text, conn, intent_hint=args.intent_hint)

    if args.message:
        print(build_resolution_message(result))
        print()

    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
