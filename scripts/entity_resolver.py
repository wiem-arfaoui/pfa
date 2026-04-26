from __future__ import annotations

import re
from typing import Any

import psycopg

from intent_router import IntentResult, normalize_query


SUBJECT_ALIASES = {
    "developpement mobile": (
        "dev mobile",
        "developpement mobile",
        "développement mobile",
        "mobile",
    ),
    "bus com & int": (
        "bus",
        "bus de communication",
        "bus communication",
        "bus com",
        "bus com int",
        "bus com & int",
    ),
    "intelli. artificielle": (
        "ai",
        "ia",
        "intelligence artificielle",
        "intelli artificielle",
        "intelli. artificielle",
    ),
    "prot & reconf.dyn": (
        "vhdl",
        "prot reconf",
        "prototypage",
        "reconfiguration dynamique",
        "prot & reconf.dyn",
    ),
    "linux embarque": (
        "linux",
        "linux embarque",
        "linux embarqué",
    ),
}


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_query(value))


def group_aliases(group_name: str) -> set[str]:
    normalized = normalize_query(group_name)
    compacted = compact(group_name)
    aliases = {normalized, compacted}

    match = re.match(r"^(?P<prefix>[a-z]+)\s+(?P<year>[123])(?P<letter>[a-z])$", normalized)
    if match:
        prefix = match.group("prefix")
        year = match.group("year")
        letter = match.group("letter")
        prefixes = {prefix}
        if prefix == "info":
            prefixes.update({"inf", "informatique"})
        elif prefix == "meca":
            prefixes.update({"m", "mecatronique"})
        elif prefix == "gsi":
            prefixes.update({"infotronique"})
        elif prefix == "gsil":
            prefixes.update({"indus", "industrielle"})

        for item in prefixes:
            aliases.add(f"{item} {year}{letter}")
            aliases.add(f"{item} {year} {letter}")
            aliases.add(f"{item}{year}{letter}")

    if normalized.startswith("gsil 3 "):
        specialty = normalized.replace("gsil 3 ", "").strip()
        aliases.add(specialty)
        aliases.add(f"gsil {specialty}")
        aliases.add(f"indus {specialty}")
        aliases.add(f"industrielle {specialty}")

    return {alias for alias in aliases if alias}


def resolve_group(conn: psycopg.Connection[Any], question: str) -> str | None:
    normalized_question = normalize_query(question)
    compact_question = compact(question)

    with conn.cursor() as cur:
        cur.execute("SELECT nom_groupe FROM groupes ORDER BY LENGTH(nom_groupe) DESC")
        groups = [row[0] for row in cur.fetchall()]

    for group_name in groups:
        for alias in group_aliases(group_name):
            alias_normalized = normalize_query(alias)
            alias_compact = compact(alias)
            if re.search(rf"\b{re.escape(alias_normalized)}\b", normalized_question):
                return group_name
            if alias_compact and alias_compact in compact_question:
                return group_name
    return None


def resolve_student_hint(conn: psycopg.Connection[Any], question: str) -> str | None:
    normalized_question = normalize_query(question)

    with conn.cursor() as cur:
        cur.execute("SELECT nom_complet, email FROM etudiants")
        rows = cur.fetchall()

    best_name: str | None = None
    best_score = 0
    for name, email in rows:
        candidates = [name]
        if email:
            local_part = str(email).split("@", 1)[0].replace(".", " ")
            candidates.append(local_part)
        for candidate in candidates:
            normalized_candidate = normalize_query(str(candidate))
            tokens = [token for token in normalized_candidate.split() if len(token) > 1]
            if not tokens:
                continue
            matches = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized_question))
            if matches > best_score and matches >= min(2, len(tokens)):
                best_score = matches
                best_name = str(name)
    return best_name


def resolve_student_context(conn: psycopg.Connection[Any], question: str) -> dict[str, Any] | None:
    normalized_question = normalize_query(question)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.nom_complet, e.email, g.nom_groupe
            FROM etudiants e
            JOIN groupes g ON g.id = e.groupe_id
            """
        )
        rows = cur.fetchall()

    best: dict[str, Any] | None = None
    best_score = 0
    for name, email, group_name in rows:
        candidates = [name]
        if email:
            candidates.append(str(email).split("@", 1)[0].replace(".", " "))

        for candidate in candidates:
            normalized_candidate = normalize_query(str(candidate))
            tokens = [token for token in normalized_candidate.split() if len(token) > 1]
            if not tokens:
                continue
            matches = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized_question))
            required_matches = min(2, len(tokens))
            if matches > best_score and matches >= required_matches:
                best_score = matches
                best = {
                    "student_name": str(name),
                    "student_email": str(email) if email else None,
                    "group": str(group_name),
                }
    return best


def resolve_subject_hint(conn: psycopg.Connection[Any], question: str) -> str | None:
    normalized_question = normalize_query(question)
    question_tokens = [token for token in normalized_question.split() if len(token) > 2]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT nom FROM matieres
            UNION
            SELECT matiere FROM emplois_temps
            UNION
            SELECT enseignant FROM emplois_temps WHERE enseignant IS NOT NULL
            """
        )
        rows = [row[0] for row in cur.fetchall() if row[0]]

    best_value: str | None = None
    best_score = 0
    for value in rows:
        normalized_value = normalize_query(str(value))
        alias_match_score = subject_alias_score(normalized_question, normalized_value)
        if alias_match_score > best_score:
            best_score = alias_match_score
            best_value = str(value)
            continue

        value_tokens = [token for token in normalized_value.split() if len(token) > 2]
        if not value_tokens:
            continue
        matches = 0
        for q_token in question_tokens:
            for v_token in value_tokens:
                if q_token == v_token:
                    matches += 1
                    break
                if len(q_token) >= 4 and len(v_token) >= 3 and (q_token.startswith(v_token) or v_token.startswith(q_token)):
                    matches += 1
                    break
        if matches > best_score and matches >= 1:
            best_score = matches
            best_value = str(value)
    return best_value


def resolve_teacher_hint(conn: psycopg.Connection[Any], question: str) -> str | None:
    normalized_question = normalize_query(question)
    question_tokens = [token for token in normalized_question.split() if len(token) > 1]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT enseignant
            FROM emplois_temps
            WHERE enseignant IS NOT NULL AND TRIM(enseignant) <> ''
            """
        )
        rows = [row[0] for row in cur.fetchall() if row[0]]

    best_value: str | None = None
    best_score = 0
    for value in rows:
        parts = [part.strip() for part in str(value).split("/") if part.strip()]
        for part in parts:
            normalized_value = normalize_query(part)
            value_tokens = [token for token in normalized_value.split() if len(token) > 1]
            if not value_tokens:
                continue

            matches = 0
            for q_token in question_tokens:
                for v_token in value_tokens:
                    if q_token == v_token:
                        matches += 1
                        break
                    if len(q_token) >= 3 and len(v_token) >= 3 and (q_token.startswith(v_token) or v_token.startswith(q_token)):
                        matches += 1
                        break

            if matches > best_score and matches >= 1:
                best_score = matches
                best_value = part
    return best_value


def subject_alias_score(normalized_question: str, normalized_value: str) -> int:
    for canonical, aliases in SUBJECT_ALIASES.items():
        normalized_canonical = normalize_query(canonical)
        if normalized_canonical != normalized_value:
            continue
        for alias in aliases:
            normalized_alias = normalize_query(alias)
            if re.search(rf"\b{re.escape(normalized_alias)}\b", normalized_question):
                return 10 + len(normalized_alias.split())
    return 0


def resolve_entities(conn: psycopg.Connection[Any], question: str, route: IntentResult) -> IntentResult:
    if route.entities.get("group"):
        enrich_from_group(conn, route, route.entities["group"])

    if not route.entities.get("group"):
        group = resolve_group(conn, question)
        if group:
            route.entities["group"] = group
            enrich_from_group(conn, route, group)

    if route.intent in {"etudiants", "emploi_temps", "salles", "enseignants"}:
        student_context = resolve_student_context(conn, question)
        if student_context:
            route.entities.setdefault("hint", student_context["student_name"])
            route.entities["student_name"] = student_context["student_name"]
            route.entities["student_email"] = student_context["student_email"]
            if not route.entities.get("group"):
                route.entities["group"] = student_context["group"]
                enrich_from_group(conn, route, student_context["group"])

    subject_markers = (
        "cours de", "matiere", "module", "ue de", "enseigne", "etudie", "etudier",
        "enseignant", "prof", "professeur"
    )
    normalized_question = normalize_query(question)
    should_resolve_subject = (
        route.intent in {"plan_etudes", "salles"}
        or (route.intent == "emploi_temps" and any(marker in normalized_question for marker in subject_markers))
    )
    if route.intent == "plan_etudes":
        specific_subject_markers = (
            "cours de",
            "matiere de",
            "module de",
            "coefficient de",
            "coeff de",
            "volume de",
        )
        should_resolve_subject = any(marker in normalized_question for marker in specific_subject_markers)
    if should_resolve_subject:
        subject = resolve_subject_hint(conn, question)
        if subject:
            route.entities["subject_hint"] = subject
            if route.intent != "emploi_temps" or not route.entities.get("student_name"):
                route.entities["hint"] = subject

    if route.intent == "enseignants":
        teacher = resolve_teacher_hint(conn, question)
        if teacher:
            route.entities["teacher_hint"] = teacher
            route.entities["hint"] = teacher

    route.missing_entities = [
        item for item in route.missing_entities
        if not (item == "groupe" and route.entities.get("group"))
        and not (item == "groupe_ou_nom_etudiant" and (route.entities.get("group") or route.entities.get("hint")))
        and not (item == "semestre_ou_annee_ou_ue" and (route.entities.get("semester") or route.entities.get("year") or route.entities.get("ue_code")))
    ]
    return route


def enrich_from_group(conn: psycopg.Connection[Any], route: IntentResult, group_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.annee, f.code, f.nom
            FROM groupes g
            JOIN formations f ON f.id = g.formation_id
            WHERE g.nom_groupe = %s
            """,
            (group_name,),
        )
        row = cur.fetchone()

    if not row:
        return

    year, formation_code, formation_name = row
    route.entities.setdefault("year", year)
    route.entities.setdefault("formation_code", formation_code)
    route.entities.setdefault("formation", normalize_query(str(formation_name)))
