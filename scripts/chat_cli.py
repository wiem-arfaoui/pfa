from __future__ import annotations

import argparse
import re
import sys
from typing import Any

import psycopg

from db_utils import load_env_file, normalize_text
from entity_resolver import resolve_entities
from intent_router import IntentResult, normalize_query, parse_question
from ollama_client import OllamaClient
from rag_retriever import RagRetriever, format_rag_context


EXIT_COMMANDS = {"exit", "quit", "q", "bye"}


def sql_like_pattern(value: str) -> str:
    normalized = normalize_query(value)
    tokens = [token for token in re.split(r"\s+", normalized) if token]
    return "%" + "%".join(tokens) + "%"


def split_slash_parts(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split("/") if part.strip()]


def teacher_specific_view(
    teacher: str | None,
    subject: str | None,
    room: str | None,
    teacher_hint: str | None,
) -> tuple[str | None, str | None, str | None]:
    if not teacher or not teacher_hint:
        return teacher, subject, room

    teacher_parts = split_slash_parts(teacher)
    if len(teacher_parts) <= 1:
        return teacher, subject, room

    normalized_hint = normalize_query(teacher_hint)
    selected_index = None
    for idx, part in enumerate(teacher_parts):
        normalized_part = normalize_query(part)
        if normalized_hint == normalized_part or normalized_hint in normalized_part or normalized_part in normalized_hint:
            selected_index = idx
            break

    if selected_index is None:
        return teacher, subject, room

    subject_parts = split_slash_parts(subject)
    room_parts = split_slash_parts(room)

    selected_teacher = teacher_parts[selected_index]
    selected_subject = subject_parts[selected_index] if len(subject_parts) == len(teacher_parts) else subject
    selected_room = room_parts[selected_index] if len(room_parts) == len(teacher_parts) else room
    return selected_teacher, selected_subject, selected_room


def connect_db() -> psycopg.Connection[Any]:
    env = load_env_file()
    database_url = env.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL est introuvable dans .env")
    return psycopg.connect(database_url)


def ask_for_missing(route: IntentResult) -> str | None:
    if not route.missing_entities:
        return None
    labels = {
        "groupe": "Veuillez preciser le groupe. Exemple: GSI 2A, INFO 1B, MECA 3C.",
        "groupe_ou_nom_etudiant": "Veuillez preciser le groupe ou le nom de l'etudiant.",
        "formation": "Veuillez preciser la formation: informatique, infotronique, mecatronique ou industrielle.",
        "semestre_ou_annee_ou_ue": "Veuillez preciser le semestre, l'annee ou le code UE. Exemple: semestre 2, 2eme annee, UE 2.1.",
    }
    return "\n".join(labels.get(item, f"Information manquante: {item}") for item in route.missing_entities)


def context_emploi(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    group_name = route.entities.get("group")
    day = route.entities.get("day")
    room = route.entities.get("room")
    session_type = route.entities.get("session_type")
    hint = route.entities.get("subject_hint")

    params: list[Any] = [group_name]
    filters = ["g.nom_groupe = %s"]

    if day:
        filters.append("et.jour = %s")
        params.append(day)
    if room:
        filters.append("LOWER(et.salle) = LOWER(%s)")
        params.append(room)
    if session_type:
        filters.append("LOWER(et.type_seance) = LOWER(%s)")
        params.append(session_type)
    if hint:
        filters.append("(LOWER(et.matiere) LIKE LOWER(%s) OR LOWER(et.enseignant) LIKE LOWER(%s))")
        pattern = sql_like_pattern(hint)
        params.extend([pattern, pattern])

    query = f"""
        SELECT et.jour, et.heure_debut, et.heure_fin, et.matiere, et.enseignant, et.salle, et.type_seance
        FROM emplois_temps et
        JOIN groupes g ON g.id = et.groupe_id
        WHERE {" AND ".join(filters)}
        ORDER BY
            CASE et.jour
                WHEN 'lundi' THEN 1
                WHEN 'mardi' THEN 2
                WHEN 'mercredi' THEN 3
                WHEN 'jeudi' THEN 4
                WHEN 'vendredi' THEN 5
                WHEN 'samedi' THEN 6
                ELSE 7
            END,
            et.heure_debut
    """

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        return f"Aucun emploi du temps trouve pour {group_name} avec ces criteres."

    title = f"Emploi du temps de {group_name}"
    if day:
        title += f" le {day}"
    lines = [title + ":"]
    current_day = None
    for row_day, start, end, subject, teacher, room_name, row_type in rows:
        if row_day != current_day:
            current_day = row_day
            lines.append(f"\n{row_day.capitalize()}:")
        line = f"- {start:%H:%M}-{end:%H:%M}: {subject}"
        if row_type:
            line += f" ({row_type})"
        if teacher:
            line += f", enseignant: {teacher}"
        if room_name:
            line += f", salle: {room_name}"
        lines.append(line)
    return "\n".join(lines)


def context_etudiants(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    group_name = route.entities.get("group")
    hint = route.entities.get("hint")
    params: list[Any] = []
    filters: list[str] = []

    if group_name:
        filters.append("g.nom_groupe = %s")
        params.append(group_name)
    if hint:
        filters.append("(LOWER(e.nom_complet) LIKE LOWER(%s) OR LOWER(e.email) LIKE LOWER(%s))")
        params.extend([f"%{hint}%", f"%{hint}%"])

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT g.nom_groupe, e.nom_complet, e.email
            FROM etudiants e
            JOIN groupes g ON g.id = e.groupe_id
            {where_clause}
            ORDER BY g.nom_groupe, e.nom_complet
            LIMIT 200
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return "Aucun etudiant trouve avec ces criteres."

    lines = ["Etudiants trouves:"]
    for group, name, email in rows:
        if email:
            lines.append(f"- {group}: {name} ({email})")
        else:
            lines.append(f"- {group}: {name}")
    return "\n".join(lines)


def context_groupes(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    formation_code = route.entities.get("formation_code")
    year = route.entities.get("year")
    params: list[Any] = []
    filters: list[str] = []

    if formation_code:
        filters.append("f.code = %s")
        params.append(formation_code)
    if year:
        filters.append("g.annee = %s")
        params.append(year)

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT f.nom, g.annee, g.nom_groupe
            FROM groupes g
            JOIN formations f ON f.id = g.formation_id
            {where_clause}
            ORDER BY f.nom, g.annee, g.nom_groupe
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return "Aucun groupe trouve."

    return "\n".join(["Groupes trouves:"] + [f"- {formation}, annee {year}: {group}" for formation, year, group in rows])


def context_description(conn: psycopg.Connection[Any], route: IntentResult, question: str = "") -> str:
    formation_code = route.entities.get("formation_code")
    formation_codes = route.entities.get("formation_codes") or ([formation_code] if formation_code else [])
    rag_query = question or route.entities.get("hint") or route.entities.get("formation") or "description formation"

    try:
        retriever = RagRetriever()
        chunks = []
        if len(formation_codes) >= 2:
            for code in formation_codes:
                chunks.extend(
                    retriever.search(
                        conn,
                        rag_query,
                        formation_code=code,
                        document_type="description",
                        top_k=1,
                    )
                )
        else:
            chunks = retriever.search(
                conn,
                rag_query,
                formation_code=formation_code,
                document_type="description",
                top_k=4,
            )
        if chunks:
            return format_rag_context(chunks, rag_query)
    except Exception as exc:
        rag_error = str(exc)
    else:
        rag_error = "Aucun chunk avec embedding trouve."

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.nom, d.contenu_texte
            FROM documents d
            JOIN formations f ON f.id = d.formation_id
            WHERE f.code = %s
              AND d.type_document = 'description'
            LIMIT 1
            """,
            (formation_code,),
        )
        row = cur.fetchone()

    if not row:
        return "Je n'ai pas trouve la description de cette formation dans la base."

    formation_name, content = row
    return (
        "La recherche RAG n'a pas pu etre executee, donc voici un extrait textuel direct.\n"
        f"Cause RAG: {rag_error}\n\n"
        f"Description de la formation {formation_name}:\n{normalize_text(content)[:1200].strip()}"
    )


def context_plan(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    formation_code = route.entities.get("formation_code")
    semester = route.entities.get("semester")
    year = route.entities.get("year")
    ue_code = route.entities.get("ue_code")
    hint = route.entities.get("hint")

    params: list[Any] = []
    filters: list[str] = []
    if formation_code:
        filters.append("f.code = %s")
        params.append(formation_code)
    if semester:
        filters.append("s.numero = %s")
        params.append(semester)
    if year:
        filters.append("n.annee = %s")
        params.append(year)
    if ue_code:
        filters.append("ue.code_ue = %s")
        params.append(ue_code)
    if hint:
        filters.append("(LOWER(m.nom) LIKE LOWER(%s) OR LOWER(ue.nom_ue) LIKE LOWER(%s))")
        params.extend([f"%{hint}%", f"%{hint}%"])

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT f.nom, n.annee, s.numero, ue.code_ue, m.nom, m.ci, m.td, m.tp, m.total, m.coefficient,
                   m.evaluation_cc, m.evaluation_examen
            FROM matieres m
            JOIN unites_enseignement ue ON ue.id = m.ue_id
            JOIN semestres s ON s.id = ue.semestre_id
            JOIN niveaux n ON n.id = s.niveau_id
            JOIN formations f ON f.id = n.formation_id
            {where_clause}
            ORDER BY f.nom, s.numero, ue.code_ue, m.id
            LIMIT 200
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return "Aucune information de plan d'etudes trouvee avec ces criteres."

    lines = ["Plan d'etudes:"]
    current_ue = None
    for formation, row_year, row_semester, code_ue, subject, ci, td, tp, total, coeff, cc, exam in rows:
        ue_label = (formation, row_year, row_semester, code_ue)
        if ue_label != current_ue:
            current_ue = ue_label
            lines.append(f"\n{formation} - annee {row_year} - semestre {row_semester} - {code_ue}:")
        details = f"- {subject}"
        if coeff is not None:
            details += f", coeff {coeff:g}"
        if total is not None:
            details += f", total {total:g}h"
        evals = [label for label, value in (("CC", cc), ("Examen", exam)) if value]
        if evals:
            details += f", evaluation: {' + '.join(evals)}"
        lines.append(details)
    return "\n".join(lines)


def context_calendrier(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    hint = route.entities.get("hint")
    params: list[Any] = []
    filter_clause = ""
    if hint:
        filter_clause = "WHERE LOWER(libelle) LIKE LOWER(%s)"
        params.append(f"%{hint}%")

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT date_debut, date_fin, libelle, source_label
            FROM evenements_calendrier
            {filter_clause}
            ORDER BY date_debut NULLS LAST, id
            LIMIT 50
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return "Aucun evenement calendrier trouve avec ces criteres."

    lines = ["Calendrier:"]
    for start, end, label, source in rows:
        if start and end and start != end:
            date_text = f"{start} -> {end}"
        elif start:
            date_text = str(start)
        else:
            date_text = source or "date non normalisee"
        lines.append(f"- {date_text}: {label}")
    return "\n".join(lines)


def context_enseignants(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    hint = route.entities.get("hint")
    teacher_hint = route.entities.get("teacher_hint")
    group_name = route.entities.get("group")
    day = route.entities.get("day")
    params: list[Any] = []
    filters: list[str] = []

    if hint:
        filters.append("(LOWER(et.enseignant) LIKE LOWER(%s) OR LOWER(et.matiere) LIKE LOWER(%s))")
        params.extend([f"%{hint}%", f"%{hint}%"])
    if group_name:
        filters.append("g.nom_groupe = %s")
        params.append(group_name)
    if day:
        filters.append("et.jour = %s")
        params.append(day)

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT et.enseignant, et.matiere, g.nom_groupe, et.jour, et.heure_debut, et.salle
            FROM emplois_temps et
            JOIN groupes g ON g.id = et.groupe_id
            {where_clause}
            ORDER BY et.enseignant, et.jour, et.heure_debut
            LIMIT 100
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return "Aucune information enseignant trouvee avec ces criteres."

    lines = ["Informations enseignants:"]
    for teacher, subject, group, row_day, start, room in rows:
        teacher, subject, room = teacher_specific_view(teacher, subject, room, teacher_hint)
        lines.append(f"- {teacher}: {subject}, {group}, {row_day} {start:%H:%M}, salle {room}")
    return "\n".join(lines)


def context_salles(conn: psycopg.Connection[Any], route: IntentResult) -> str:
    room = route.entities.get("room")
    group_name = route.entities.get("group")
    day = route.entities.get("day")
    time_period = route.entities.get("time_period")
    subject_hint = route.entities.get("subject_hint") or route.entities.get("hint")
    params: list[Any] = []
    filters: list[str] = []

    if room:
        filters.append("LOWER(et.salle) = LOWER(%s)")
        params.append(room)
    if group_name:
        filters.append("g.nom_groupe = %s")
        params.append(group_name)
    if day:
        filters.append("et.jour = %s")
        params.append(day)
    if subject_hint:
        filters.append("LOWER(et.matiere) LIKE LOWER(%s)")
        params.append(sql_like_pattern(subject_hint))
    if time_period == "morning":
        filters.append("et.heure_debut < TIME '12:00'")
    elif time_period == "afternoon":
        filters.append("et.heure_debut >= TIME '12:00' AND et.heure_debut < TIME '18:00'")
    elif time_period == "evening":
        filters.append("et.heure_debut >= TIME '18:00'")

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT et.salle, et.jour, et.heure_debut, et.heure_fin, et.matiere, et.enseignant, g.nom_groupe
            FROM emplois_temps et
            JOIN groupes g ON g.id = et.groupe_id
            {where_clause}
            ORDER BY et.salle, et.jour, et.heure_debut
            LIMIT 100
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        return "Aucune information de salle trouvee avec ces criteres."

    title = "Informations salles"
    if group_name:
        title += f" pour {group_name}"
    if subject_hint:
        title += f" pour le cours {subject_hint}"
    if day:
        title += f" le {day}"
    if time_period == "morning":
        title += " matin"
    elif time_period == "afternoon":
        title += " apres-midi"
    elif time_period == "evening":
        title += " soir"

    lines = [title + ":"]
    for room_name, row_day, start, end, subject, teacher, group in rows:
        lines.append(f"- {room_name}: {row_day} {start:%H:%M}-{end:%H:%M}, {subject}, {group}, enseignant {teacher}")
    return "\n".join(lines)


def build_context(conn: psycopg.Connection[Any], route: IntentResult, question: str = "") -> str:
    handlers = {
        "emploi_temps": context_emploi,
        "etudiants": context_etudiants,
        "groupes": context_groupes,
        "formation_description": context_description,
        "plan_etudes": context_plan,
        "calendrier": context_calendrier,
        "enseignants": context_enseignants,
        "salles": context_salles,
    }
    handler = handlers.get(route.intent)
    if not handler:
        return (
            "Type de question non reconnu. Exemples possibles: emploi du temps de GSI 2A lundi, "
            "etudiants INFO 1A, modules semestre 2 informatique, date des examens."
        )
    if route.intent == "formation_description":
        return handler(conn, route, question)
    return handler(conn, route)


def build_llm_metadata(route: IntentResult) -> str:
    parts: list[str] = [f"intent={route.intent}"]
    if route.entities.get("formations"):
        parts.append(f"formations_cibles={', '.join(route.entities['formations'])}")
        if len(route.entities["formations"]) >= 2:
            parts.append("type_question=comparaison_entre_formations")
    if route.entities.get("student_name"):
        parts.append(f"personne_cible={route.entities['student_name']}")
        parts.append("role_personne_cible=etudiant")
    if route.entities.get("student_email"):
        parts.append(f"email_personne_cible={route.entities['student_email']}")
    if route.entities.get("group"):
        parts.append(f"groupe_cible={route.entities['group']}")
    if route.entities.get("day"):
        parts.append(f"jour_cible={route.entities['day']}")
    if route.entities.get("subject_hint"):
        parts.append(f"matiere_cible={route.entities['subject_hint']}")
    elif route.entities.get("hint") and not route.entities.get("student_name"):
        parts.append(f"indice_cible={route.entities['hint']}")
    parts.append(
        "regle=si la personne cible est un etudiant, repondre avec son emploi du temps/groupe/salle et non comme enseignant"
    )
    return "\n".join(parts)


def answer(
    conn: psycopg.Connection[Any],
    question: str,
    ollama: OllamaClient | None = None,
    debug: bool = False,
) -> str:
    route = parse_question(question)
    route = resolve_entities(conn, question, route)
    missing_answer = ask_for_missing(route)
    if missing_answer:
        return missing_answer

    context = build_context(conn, route, question)
    debug_text = ""
    if debug:
        debug_label = "INTENT + RAG" if route.intent == "formation_description" else "INTENT + SQL"
        debug_text = (
            f"[DEBUG: {debug_label}]\n"
            f"intent={route.intent}\n"
            f"confidence={route.confidence:.2f}\n"
            f"entities={route.entities}\n"
            f"sources={route.sources}\n"
            f"context=\n{context}\n"
            f"[/DEBUG: {debug_label}]\n\n"
        )

    if ollama is None:
        return debug_text + context

    try:
        final_answer = ollama.reformulate(
            question,
            context,
            metadata=build_llm_metadata(route),
            intent=route.intent,
        )
    except Exception as exc:
        final_answer = (
            "Ollama n'a pas pu reformuler la reponse. Voici le resultat brut:\n"
            f"{context}\n\n"
            f"Erreur Ollama: {exc}"
        )
    if debug:
        return debug_text + "[LLM REFORMULATION]\n" + final_answer + "\n[/LLM REFORMULATION]"
    return final_answer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat text-to-text local ENICarthage.")
    parser.add_argument("--debug", action="store_true", help="Affiche intention, entites, sources et contexte.")
    parser.add_argument("--no-llm", action="store_true", help="Desactive Ollama et affiche le contexte SQL brut.")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = build_parser().parse_args()
    ollama = None if args.no_llm else OllamaClient()
    print("Assistant ENICarthage pret. Tapez 'exit' pour quitter.")
    with connect_db() as conn:
        while True:
            question = input("\nVous: ").strip()
            if normalize_query(question) in EXIT_COMMANDS:
                print("Bot: A bientot.")
                break
            if not question:
                continue
            try:
                print("\nBot:")
                print(answer(conn, question, ollama=ollama, debug=args.debug))
            except Exception as exc:
                print(f"\nBot: Une erreur est survenue: {exc}")


if __name__ == "__main__":
    main()
