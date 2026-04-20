from __future__ import annotations

import requests

from db_utils import load_env_file


SYSTEM_PROMPT = """Tu es un assistant universitaire pour l'ENICarthage.
Tu reponds uniquement a partir du contexte fourni.
Tu ne dois pas inventer d'information.
Si l'information n'existe pas dans le contexte, dis clairement qu'elle n'est pas disponible dans la base.
Reponds en francais clair, utile et concis.
Garde les horaires, noms de groupes, matieres, salles, emails et dates exactement comme dans le contexte.
Respecte strictement les roles des personnes mentionnees dans les meta-donnees.
Si les meta-donnees disent qu'une personne est un etudiant, ne la traite jamais comme un enseignant.
Ne confonds jamais la personne cible de la question avec les enseignants listes dans l'emploi du temps.
"""


class OllamaClient:
    def __init__(self) -> None:
        env = load_env_file()
        self.base_url = env.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = env.get("OLLAMA_LLM_MODEL", "llama3:latest")

    def reformulate(self, question: str, context: str, metadata: str = "") -> str:
        if not context.strip():
            return "Je n'ai pas trouve d'information disponible dans la base pour cette question."

        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
            },
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Question utilisateur:\n"
                        f"{question}\n\n"
                        "Meta-donnees de raisonnement:\n"
                        f"{metadata or 'Aucune'}\n\n"
                        "Contexte recupere depuis la base de donnees:\n"
                        f"{context}\n\n"
                        "Redige la reponse finale en francais."
                    ),
                },
            ],
        }

        response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "").strip()
        if not content:
            return context
        return content
