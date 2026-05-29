from __future__ import annotations

import argparse
import sys
from typing import Any

import psycopg

from db_utils import load_env_file
from rag_retriever import RagRetriever, format_rag_context


def connect_db() -> psycopg.Connection[Any]:
    env = load_env_file()
    database_url = env.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL est introuvable dans .env")
    return psycopg.connect(database_url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test RAG local avec Ollama embeddinggemma et pgvector.")
    parser.add_argument("question", help="Question textuelle a rechercher dans les documents.")
    parser.add_argument("--formation-code", help="Filtrer par code formation: info, infotronique, mecatronique, industrielle.")
    parser.add_argument("--top-k", type=int, default=4, help="Nombre de passages a retourner.")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    retriever = RagRetriever()
    with connect_db() as conn:
        chunks = retriever.search(
            conn,
            args.question,
            formation_code=args.formation_code,
            document_type="description",
            top_k=args.top_k,
        )
    print(format_rag_context(chunks, args.question))


if __name__ == "__main__":
    main()
