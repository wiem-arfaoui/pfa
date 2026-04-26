from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class IntentResult:
    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    missing_entities: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


FORMATION_ALIASES = {
    "informatique": ("informatique", "info", "genie informatique", "gi informatique"),
    "infotronique": ("infotronique", "gsi", "gsinfotronique", "systemes infotroniques"),
    "mecatronique": ("mecatronique", "meca", "mecantronique", "m"),
    "industrielle": ("industrielle", "indus", "gsil", "genie industriel", "industriel"),
}

FORMATION_CODES = {
    "informatique": "info",
    "infotronique": "infotronique",
    "mecatronique": "mecatronique",
    "industrielle": "industrielle",
}

DAY_ALIASES = {
    "lundi": ("lundi", "lun"),
    "mardi": ("mardi", "mar"),
    "mercredi": ("mercredi", "mer"),
    "jeudi": ("jeudi", "jeu"),
    "vendredi": ("vendredi", "ven"),
    "samedi": ("samedi", "sam"),
    "dimanche": ("dimanche", "dim"),
}

SESSION_TYPE_ALIASES = {
    "ci": ("ci", "classe integree", "classe integrée", "classe intégrée"),
    "td": ("td", "travaux diriges", "travaux dirigés"),
    "tp": ("tp", "travaux pratiques"),
}

EMPLOI_KEYWORDS = (
    "emploi", "planning", "programme", "horaire", "seance", "seances", "cours",
    "salle", "quand", "ou", "où", "a quelle heure", "demarre", "commence",
)
ETUDIANT_KEYWORDS = (
    "etudiant", "etudiants", "eleve", "eleves", "email", "mail",
    "qui est dans", "membres",
)
GROUPE_KEYWORDS = ("groupe", "groupes", "classe", "classes")
PLAN_KEYWORDS = (
    "plan", "etude", "etudes", "module", "modules", "ue", "unite", "unite enseignement",
    "matiere", "matieres", "ee", "element", "coefficient", "coeff", "credit",
    "volume", "horaire semestriel", "cc", "examen",
)
DESCRIPTION_KEYWORDS = (
    "formation", "presente", "presenter", "description", "c est quoi", "objectif",
    "objectifs", "metier", "metiers", "debouche", "debouches", "stage", "stages",
)
CALENDAR_KEYWORDS = (
    "calendrier", "date", "dates", "quand", "examen", "examens", "ds", "soutenance",
    "soutenances", "rentrée", "rentree", "demarrage", "démarrage", "notes", "forum",
)
ENSEIGNANT_KEYWORDS = ("enseignant", "prof", "professeur", "enseigne", "qui enseigne")
SALLE_KEYWORDS = ("salle", "local", "amphi", "labo", "laboratoire", "ou se deroule", "où se déroule")


def normalize_query(text: str) -> str:
    text = (
        text.replace("Ã©", "é")
        .replace("Ã¨", "è")
        .replace("Ãª", "ê")
        .replace("Ã ", "à")
        .replace("Ã´", "ô")
        .replace("Ã®", "î")
        .replace("Ã»", "û")
        .replace("Ã§", "ç")
    )
    lowered = text.lower().strip()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_accents = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    without_accents = without_accents.replace("_", " ").replace("-", " ")
    without_accents = without_accents.replace("’", "'").replace("'", " ")
    without_accents = without_accents.replace("?", " ").replace(",", " ")
    without_accents = without_accents.replace("&", " & ")
    return re.sub(r"\s+", " ", without_accents).strip()


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    for keyword in keywords:
        normalized_keyword = normalize_query(keyword)
        if not normalized_keyword:
            continue
        if len(normalized_keyword) <= 3:
            if re.search(rf"\b{re.escape(normalized_keyword)}\b", text):
                return True
        elif normalized_keyword in text:
            return True
    return False


def detect_formation(text: str) -> str | None:
    for canonical, aliases in FORMATION_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_query(alias)
            if re.search(rf"\b{re.escape(normalized_alias)}\b", text):
                return canonical
    return None


def detect_year(text: str) -> int | None:
    patterns = [
        (1, r"\b(1|1ere|1er|premiere|ing1)\b"),
        (2, r"\b(2|2eme|deuxieme|ing2)\b"),
        (3, r"\b(3|3eme|troisieme|ing3)\b"),
    ]
    for year, pattern in patterns:
        if re.search(pattern, text):
            return year
    return None


def detect_semester(text: str) -> int | None:
    match = re.search(r"\b(?:s|sem|semestre)\s*([1-6])\b", text)
    if match:
        return int(match.group(1))
    words = {
        1: ("premier semestre", "semestre un"),
        2: ("deuxieme semestre", "semestre deux"),
        3: ("troisieme semestre", "semestre trois"),
        4: ("quatrieme semestre", "semestre quatre"),
        5: ("cinquieme semestre", "semestre cinq"),
        6: ("sixieme semestre", "semestre six"),
    }
    for semester, aliases in words.items():
        if any(alias in text for alias in aliases):
            return semester
    return None


def detect_day(text: str) -> str | None:
    relative_day = detect_relative_day(text)
    if relative_day:
        return relative_day

    for canonical, aliases in DAY_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases):
            return canonical
    return None


def detect_relative_day(text: str) -> str | None:
    weekdays = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    today = date.today()
    if re.search(r"\b(aujourd hui|aujourdhui|today)\b", text):
        return weekdays[today.weekday()]
    if re.search(r"\b(demain|tomorrow)\b", text):
        return weekdays[(today + timedelta(days=1)).weekday()]
    if re.search(r"\b(hier|yesterday)\b", text):
        return weekdays[(today - timedelta(days=1)).weekday()]
    return None


def detect_group(text: str) -> str | None:
    patterns = [
        r"\b(?P<prefix>gsi|info|inf|meca|m|gsil)\s*(?P<year>[123])\s*(?P<letter>[abcd])\b",
        r"\b(?P<prefix>gsi|info|inf|meca|m|gsil)\s*(?P<year>[123])(?P<letter>[abcd])\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        prefix = match.group("prefix")
        year = match.group("year")
        letter = match.group("letter").upper()
        if prefix in {"info", "inf"}:
            return f"INFO {year}{letter}"
        if prefix == "gsi":
            return f"GSI {year}{letter}"
        if prefix in {"m", "meca"}:
            return f"MECA {year}{letter}"
        if prefix == "gsil":
            return f"GSIL {year}{letter}"

    return None


def detect_ue_code(text: str) -> str | None:
    match = re.search(r"\bue\s*([1-6])\s*[.\- ]\s*([1-9])\b", text)
    if match:
        return f"UE {match.group(1)}.{match.group(2)}"
    return None


def detect_session_type(text: str) -> str | None:
    for canonical, aliases in SESSION_TYPE_ALIASES.items():
        if any(re.search(rf"\b{re.escape(normalize_query(alias))}\b", text) for alias in aliases):
            return canonical
    return None


def detect_room(text: str) -> str | None:
    match = re.search(r"\b(?:salle|local|labo|laboratoire|amphi)\s*([a-z]{0,2}\d{1,3})\b", text)
    if match:
        return match.group(1)
    return None


def detect_time_period(text: str) -> str | None:
    if re.search(r"\b(matin|matinee|matinée)\b", text):
        return "morning"
    if re.search(r"\b(apres midi|apres midi|apres-midi|apresmidi)\b", text):
        return "afternoon"
    if re.search(r"\b(soir|soiree|soirée)\b", text):
        return "evening"
    return None


def detect_person_or_subject_hint(text: str) -> str | None:
    patterns = [
        r"([a-z][a-z ]{2,40})\s+(?:est|se trouve|appartient)\s+(?:dans\s+)?(?:quel\s+)?groupe",
        r"(?:groupe de|groupe d'|groupe pour)\s+([a-z][a-z ]{2,40})",
        r"(?:enseignant|prof|professeur|mr|mme|dr)\s+([a-z][a-z ]{2,40})",
        r"(?:cours de|matiere|module|ue de|enseigne)\s+([a-z][a-z0-9 .&']{2,60}?)(?=\s+(?:pour|du groupe|de groupe|a|au|aux|chez|dans)\b|$)",
        r"(?:email de|mail de)\s+([a-z][a-z ]{2,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def choose_intent(text: str, entities: dict[str, Any]) -> tuple[str, float]:
    scores = {
        "emploi_temps": 0,
        "etudiants": 0,
        "groupes": 0,
        "plan_etudes": 0,
        "formation_description": 0,
        "calendrier": 0,
        "enseignants": 0,
        "salles": 0,
    }

    if has_any(text, EMPLOI_KEYWORDS):
        scores["emploi_temps"] += 3
    if has_any(text, ETUDIANT_KEYWORDS):
        scores["etudiants"] += 4
    if has_any(text, GROUPE_KEYWORDS):
        scores["groupes"] += 2
    if has_any(text, PLAN_KEYWORDS):
        scores["plan_etudes"] += 4
    if has_any(text, DESCRIPTION_KEYWORDS):
        scores["formation_description"] += 3
    if has_any(text, CALENDAR_KEYWORDS):
        scores["calendrier"] += 2
    if has_any(text, ENSEIGNANT_KEYWORDS):
        scores["enseignants"] += 4
    if has_any(text, SALLE_KEYWORDS):
        scores["salles"] += 4

    if "emploi du temps" in text:
        scores["emploi_temps"] += 6
        scores["plan_etudes"] -= 3

    if entities.get("group"):
        scores["emploi_temps"] += 1
        scores["etudiants"] += 1
    if entities.get("ue_code") or entities.get("semester"):
        scores["plan_etudes"] += 2
    if entities.get("day"):
        scores["emploi_temps"] += 2
        scores["calendrier"] -= 1
    if "qui est dans" in text:
        scores["etudiants"] += 5
    if "combien" in text and entities.get("group"):
        scores["etudiants"] += 2
    if "groupe" in text or "groupes" in text:
        scores["groupes"] += 3
        if not has_any(text, ETUDIANT_KEYWORDS):
            scores["etudiants"] -= 2
    if ("dans quel groupe" in text or "quel groupe" in text or "groupe de" in text) and entities.get("hint"):
        scores["etudiants"] += 7
        scores["groupes"] -= 4
    if ("examen" in text or "examens" in text or "ds" in text) and not (
        entities.get("formation") or entities.get("group") or entities.get("semester") or entities.get("ue_code")
    ):
        scores["calendrier"] += 5
        scores["plan_etudes"] -= 3

    if scores["enseignants"] >= 4 and ("qui enseigne" in text or "enseigne" in text):
        return "enseignants", 0.92
    if scores["salles"] >= 4 and (entities.get("group") or entities.get("day") or "cours" in text):
        return "salles", 0.88

    intent, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        return "unknown", 0.0
    confidence = min(0.98, 0.45 + score * 0.08)
    return intent, confidence


def sources_for(intent: str) -> list[str]:
    return {
        "emploi_temps": ["emplois_temps", "groupes", "formations"],
        "etudiants": ["etudiants", "groupes", "formations"],
        "groupes": ["groupes", "formations"],
        "plan_etudes": ["semestres", "unites_enseignement", "matieres", "formations", "niveaux"],
        "formation_description": ["documents", "document_chunks", "formations"],
        "calendrier": ["evenements_calendrier"],
        "enseignants": ["emplois_temps"],
        "salles": ["emplois_temps"],
    }.get(intent, [])


def missing_for(intent: str, entities: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if intent == "emploi_temps" and not entities.get("group"):
        missing.append("groupe")
    if intent == "etudiants" and not entities.get("group") and not entities.get("hint"):
        missing.append("groupe_ou_nom_etudiant")
    if intent == "plan_etudes":
        if not entities.get("formation") and not entities.get("group"):
            missing.append("formation")
        if not entities.get("semester") and not entities.get("year") and not entities.get("ue_code"):
            missing.append("semestre_ou_annee_ou_ue")
    if intent == "formation_description" and not entities.get("formation"):
        missing.append("formation")
    return missing


def parse_question(question: str) -> IntentResult:
    text = normalize_query(question)
    entities: dict[str, Any] = {}

    group = detect_group(text)
    if group:
        entities["group"] = group
        year_match = re.search(r"\b([123])", group)
        if year_match:
            entities["year"] = int(year_match.group(1))

    formation = detect_formation(text)
    if formation:
        entities["formation"] = formation
        entities["formation_code"] = FORMATION_CODES[formation]

    day = detect_day(text)
    if day:
        entities["day"] = day

    semester = detect_semester(text)
    if semester:
        entities["semester"] = semester

    year = detect_year(text)
    if year and not entities.get("year"):
        entities["year"] = year

    ue_code = detect_ue_code(text)
    if ue_code:
        entities["ue_code"] = ue_code

    session_type = detect_session_type(text)
    if session_type:
        entities["session_type"] = session_type

    room = detect_room(text)
    if room:
        entities["room"] = room

    time_period = detect_time_period(text)
    if time_period:
        entities["time_period"] = time_period

    hint = detect_person_or_subject_hint(text)
    if hint:
        entities["hint"] = hint

    intent, confidence = choose_intent(text, entities)
    return IntentResult(
        intent=intent,
        confidence=confidence,
        entities=entities,
        missing_entities=missing_for(intent, entities),
        sources=sources_for(intent),
    )
