from __future__ import annotations

import requests

from db_utils import load_env_file


BASE_SYSTEM_PROMPT = """Tu es un assistant universitaire pour l'ENICarthage.
Tu reponds uniquement a partir du contexte fourni.
Tu ne dois pas inventer d'information.
Si l'information n'existe pas dans le contexte, dis clairement qu'elle n'est pas disponible dans la base.
Reponds en francais clair, utile et concis.
Garde les horaires, noms de groupes, matieres, salles, emails et dates exactement comme dans le contexte.
Respecte strictement les roles des personnes mentionnees dans les meta-donnees.
Si les meta-donnees disent qu'une personne est un etudiant, ne la traite jamais comme un enseignant.
Ne confonds jamais la personne cible de la question avec les enseignants listes dans le contexte.
Ne termine pas par une formule de politesse comme "Cordialement".
"""


INTENT_PROMPTS = {
    "emploi_temps": {
        "system": """Tu reformules un emploi du temps.
Quand le contexte contient une liste de cours ou de seances, tu dois reprendre TOUS les elements.
Tu n'as pas le droit de resumer une liste en supprimant des lignes.
Tu n'as pas le droit de choisir seulement quelques cours.
Conserve l'ordre des seances tel qu'il apparait dans le contexte.
Si la question porte sur un etudiant, reponds avec l'emploi du temps de son groupe, sans dire que l'etudiant est enseignant.
Si plusieurs cours existent le meme jour, ils doivent tous apparaitre.""",
        "user": """Format attendu:
- une phrase courte d'introduction
- puis une liste avec tous les cours, chaque ligne contenant horaire, matiere, salle et enseignant si disponibles
- aucun cours ne doit etre oublie""",
    },
    "salles": {
        "system": """Tu reformules une reponse sur les salles.
Tu dois reprendre toutes les lignes du contexte.
Si une matiere est precisee, ne parle que de cette matiere.
Si un etudiant est mentionne, la reponse doit porter sur son groupe et ses seances, pas sur la personne comme enseignant.""",
        "user": """Format attendu:
- une phrase courte qui dit pour quel groupe ou quelle personne la reponse est donnee
- puis une liste des salles avec jour, horaire, matiere et groupe si presents""",
    },
    "enseignants": {
        "system": """Tu reformules une reponse sur les enseignants.
Tu dois garder strictement le nom de l'enseignant cible.
Tu ne dois pas melanger plusieurs enseignants.
Tu dois reprendre toutes les lignes du contexte dans l'ordre.""",
        "user": """Format attendu:
- une phrase courte d'introduction
- puis une liste des seances de l'enseignant avec matiere, groupe, jour, horaire et salle""",
    },
    "etudiants": {
        "system": """Tu reformules une reponse sur les etudiants.
Tu dois garder tous les etudiants ou toutes les lignes du contexte.
Tu ne dois supprimer aucun nom ou email.""",
        "user": """Format attendu:
- une phrase courte d'introduction
- puis une liste de tous les etudiants avec leur groupe et leur email si present""",
    },
    "groupes": {
        "system": """Tu reformules une reponse sur les groupes.
Tu dois garder tous les groupes retournes dans le contexte.
Ne supprime aucune ligne.""",
        "user": """Format attendu:
- une phrase courte d'introduction
- puis une liste complete des groupes""",
    },
    "plan_etudes": {
        "system": """Tu reformules un plan d'etudes.
Tu dois garder toutes les UE et toutes les matieres listees dans le contexte.
Tu ne dois supprimer aucune matiere.
Conserve l'organisation par semestre et par UE si elle est presente.""",
        "user": """Format attendu:
- une phrase courte d'introduction
- puis les semestres et UE dans l'ordre
- sous chaque UE, garder toutes les matieres avec coefficient, volume et evaluation si disponibles""",
    },
    "calendrier": {
        "system": """Tu reformules une reponse sur le calendrier universitaire.
Tu dois reprendre toutes les dates ou periodes presentes dans le contexte.
Ne supprime aucun evenement.""",
        "user": """Format attendu:
- une phrase courte d'introduction
- puis une liste chronologique de tous les evenements""",
    },
    "formation_description": {
        "system": """Tu reformules une description de formation.
Tu peux resumer legerement, mais sans inventer d'information.
Tu dois rester fidele au contenu et garder les points importants.
Si la question compare deux formations, tu dois expliquer les differences uniquement a partir des passages fournis.
Pour une comparaison, distingue clairement l'orientation, les competences et les debouches si ces informations existent dans le contexte.""",
        "user": """Format attendu:
- un court paragraphe d'introduction
- puis 3 a 6 points cles si le contexte est riche
- si c'est une comparaison, terminer par une phrase simple qui resume la difference principale""",
    },
}


class OllamaClient:
    def __init__(self) -> None:
        env = load_env_file()
        self.base_url = env.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = env.get("OLLAMA_LLM_MODEL", "llama3:latest")

    def reformulate(self, question: str, context: str, metadata: str = "", intent: str = "unknown") -> str:
        if not context.strip():
            return "Je n'ai pas trouve d'information disponible dans la base pour cette question."

        intent_prompt = INTENT_PROMPTS.get(intent, {})
        system_prompt = BASE_SYSTEM_PROMPT
        if intent_prompt.get("system"):
            system_prompt += "\n" + intent_prompt["system"]

        user_instruction = intent_prompt.get(
            "user",
            "Redige la reponse finale en francais a partir du contexte, sans rien inventer.",
        )

        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Question utilisateur:\n"
                        f"{question}\n\n"
                        "Meta-donnees de raisonnement:\n"
                        f"{metadata or 'Aucune'}\n\n"
                        "Contexte recupere depuis la base de donnees:\n"
                        f"{context}\n\n"
                        "Consignes de reponse:\n"
                        f"{user_instruction}\n\n"
                        "Redige maintenant la reponse finale en francais."
                    ),
                },
            ],
        }

        response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=300)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "").strip()
        if not content:
            return context
        return content
