from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import psycopg
import requests

from db_utils import format_vector, load_env_file, normalize_text


STOP_WORDS = {
    "a",
    "au",
    "aux",
    "avec",
    "ce",
    "ces",
    "cette",
    "de",
    "des",
    "du",
    "en",
    "est",
    "et",
    "formation",
    "la",
    "le",
    "les",
    "pour",
    "qu",
    "que",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "sont",
    "un",
    "une",
}


INTENT_KEYWORDS = {
    "objectif": {"objectif", "objectifs", "vise", "former", "prepare", "preparer", "capable", "capables", "apte", "aptitude"},
    "debouche": {"debouche", "debouches", "metier", "metiers", "carriere", "poste", "postes", "secteur", "secteurs"},
    "competence": {"competence", "competences", "maitriser", "developper", "concevoir", "analyser", "piloter"},
}


@dataclass(frozen=True)
class RagChunk:
    formation_name: str
    document_title: str
    source_path: str | None
    chunk_index: int
    content: str
    score: float


class RagRetriever:
    def __init__(self) -> None:
        env = load_env_file()
        self.ollama_base_url = env.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.embedding_model = env.get("OLLAMA_EMBEDDING_MODEL", "embeddinggemma")
        self.embedding_dimension = int(env.get("PGVECTOR_DIMENSION", "768"))

    def embed_query(self, query: str) -> list[float]:
        clean_query = normalize_text(query)
        if not clean_query:
            raise ValueError("La question est vide, impossible de generer un embedding.")

        response = requests.post(
            f"{self.ollama_base_url}/api/embed",
            json={
                "model": self.embedding_model,
                "input": clean_query,
                "dimensions": self.embedding_dimension,
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings") or []
        if not embeddings:
            raise RuntimeError("Ollama n'a retourne aucun embedding pour la question.")
        return embeddings[0]

    def search(
        self,
        conn: psycopg.Connection[Any],
        query: str,
        *,
        formation_code: str | None = None,
        document_type: str = "description",
        top_k: int = 4,
    ) -> list[RagChunk]:
        query_embedding = self.embed_query(query)
        vector_value = format_vector(query_embedding)

        filters = ["d.type_document = %s", "dc.embedding IS NOT NULL"]
        filter_params: list[Any] = [document_type]
        if formation_code:
            filters.append("f.code = %s")
            filter_params.append(formation_code)

        where_clause = " AND ".join(filters)

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    COALESCE(f.nom, 'Formation non precisee') AS formation_name,
                    d.titre,
                    d.source_path,
                    dc.chunk_index,
                    dc.contenu,
                    1 - (dc.embedding <=> CAST(%s AS vector)) AS score
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                LEFT JOIN formations f ON f.id = d.formation_id
                WHERE {where_clause}
                ORDER BY dc.embedding <=> CAST(%s AS vector)
                LIMIT %s
                """,
                [vector_value, *filter_params, vector_value, top_k],
            )
            rows = cur.fetchall()

        return [
            RagChunk(
                formation_name=row[0],
                document_title=row[1],
                source_path=row[2],
                chunk_index=row[3],
                content=normalize_text(row[4]),
                score=float(row[5] or 0),
            )
            for row in rows
        ]


def _normalize_for_match(value: str) -> str:
    text = unicodedata.normalize("NFKD", normalize_text(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _query_terms(query: str) -> set[str]:
    normalized = _normalize_for_match(query)
    terms = {token for token in normalized.split() if len(token) > 2 and token not in STOP_WORDS}
    for trigger, keywords in INTENT_KEYWORDS.items():
        if trigger in terms or any(keyword in terms for keyword in keywords):
            terms.update(keywords)
    return terms


def _split_sentences(text: str) -> list[str]:
    clean_text = normalize_text(text)
    sentences = re.split(r"(?<=[.!?])\s+", clean_text)
    return [sentence.strip(" -\n\t") for sentence in sentences if sentence.strip()]


def _sentence_score(sentence: str, terms: set[str]) -> int:
    if not terms:
        return 0
    sentence_terms = set(_normalize_for_match(sentence).split())
    return len(sentence_terms & terms)


def _limit_text(text: str, max_chars: int = 650) -> str:
    clean_text = normalize_text(text)
    if len(clean_text) <= max_chars:
        return clean_text
    cut = clean_text[:max_chars].rsplit(" ", 1)[0].strip()
    return cut + "..."


def extract_relevant_text(content: str, query: str, max_sentences: int = 4) -> str:
    sentences = _split_sentences(content)
    if not sentences:
        return _limit_text(content)

    terms = _query_terms(query)
    scored = [
        (score, index, sentence)
        for index, sentence in enumerate(sentences)
        if (score := _sentence_score(sentence, terms)) > 0
    ]
    if not scored:
        return _limit_text(" ".join(sentences[:max_sentences]))

    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_sentences]
    selected_indexes = {index for _, index, _ in selected}
    return _limit_text(" ".join(sentence for index, sentence in enumerate(sentences) if index in selected_indexes))


def format_rag_context(chunks: list[RagChunk], query: str = "") -> str:
    if not chunks:
        return "Aucun passage textuel pertinent trouve dans la base RAG."

    lines = ["Resultats RAG depuis les descriptions textuelles:"]
    for index, chunk in enumerate(chunks, start=1):
        source = chunk.source_path or chunk.document_title
        content = extract_relevant_text(chunk.content, query) if query else chunk.content
        lines.append(
            f"\n[Passage {index} | formation={chunk.formation_name} | "
            f"score={chunk.score:.3f} | source={source} | chunk={chunk.chunk_index}]"
        )
        lines.append(content)
    return "\n".join(lines).strip()
