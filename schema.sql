CREATE EXTENSION IF NOT EXISTS vector;

BEGIN;

CREATE TABLE IF NOT EXISTS formations (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    nom VARCHAR(255) NOT NULL,
    description_courte TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS niveaux (
    id BIGSERIAL PRIMARY KEY,
    formation_id BIGINT NOT NULL REFERENCES formations(id) ON DELETE CASCADE,
    annee SMALLINT NOT NULL CHECK (annee IN (1, 2, 3)),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_niveaux_formation_annee UNIQUE (formation_id, annee)
);

CREATE TABLE IF NOT EXISTS semestres (
    id BIGSERIAL PRIMARY KEY,
    niveau_id BIGINT NOT NULL REFERENCES niveaux(id) ON DELETE CASCADE,
    numero SMALLINT NOT NULL CHECK (numero BETWEEN 1 AND 6),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_semestres_niveau_numero UNIQUE (niveau_id, numero)
);

CREATE TABLE IF NOT EXISTS unites_enseignement (
    id BIGSERIAL PRIMARY KEY,
    semestre_id BIGINT NOT NULL REFERENCES semestres(id) ON DELETE CASCADE,
    code_ue VARCHAR(50),
    nom_ue VARCHAR(255) NOT NULL,
    coefficient_ue NUMERIC(6, 2),
    credits_ue NUMERIC(6, 2),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS matieres (
    id BIGSERIAL PRIMARY KEY,
    ue_id BIGINT NOT NULL REFERENCES unites_enseignement(id) ON DELETE CASCADE,
    nom VARCHAR(255) NOT NULL,
    ci NUMERIC(6, 2),
    td NUMERIC(6, 2),
    tp NUMERIC(6, 2),
    total NUMERIC(6, 2),
    coefficient NUMERIC(6, 2),
    evaluation_cc VARCHAR(255),
    evaluation_examen VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS groupes (
    id BIGSERIAL PRIMARY KEY,
    formation_id BIGINT NOT NULL REFERENCES formations(id) ON DELETE CASCADE,
    annee SMALLINT NOT NULL CHECK (annee IN (1, 2, 3)),
    nom_groupe VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_groupes_formation_annee_nom UNIQUE (formation_id, annee, nom_groupe)
);

CREATE TABLE IF NOT EXISTS etudiants (
    id BIGSERIAL PRIMARY KEY,
    groupe_id BIGINT NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    nom_complet VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_etudiants_groupe_email UNIQUE (groupe_id, email)
);

CREATE TABLE IF NOT EXISTS emplois_temps (
    id BIGSERIAL PRIMARY KEY,
    groupe_id BIGINT NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    semestre SMALLINT NOT NULL CHECK (semestre BETWEEN 1 AND 6),
    jour VARCHAR(20) NOT NULL,
    heure_debut TIME NOT NULL,
    heure_fin TIME NOT NULL,
    matiere VARCHAR(255) NOT NULL,
    enseignant VARCHAR(255),
    salle VARCHAR(100),
    type_seance VARCHAR(50),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evenements_calendrier (
    id BIGSERIAL PRIMARY KEY,
    date_debut DATE,
    date_fin DATE,
    libelle TEXT NOT NULL,
    source_label VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    type_document VARCHAR(50) NOT NULL,
    titre VARCHAR(255) NOT NULL,
    formation_id BIGINT REFERENCES formations(id) ON DELETE SET NULL,
    niveau_id BIGINT REFERENCES niveaux(id) ON DELETE SET NULL,
    semestre_id BIGINT REFERENCES semestres(id) ON DELETE SET NULL,
    source_path TEXT,
    contenu_texte TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    contenu TEXT NOT NULL,
    embedding VECTOR(768),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_document_chunks_document_chunk UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_niveaux_formation_id
    ON niveaux(formation_id);

CREATE INDEX IF NOT EXISTS idx_semestres_niveau_id
    ON semestres(niveau_id);

CREATE INDEX IF NOT EXISTS idx_unites_enseignement_semestre_id
    ON unites_enseignement(semestre_id);

CREATE INDEX IF NOT EXISTS idx_matieres_ue_id
    ON matieres(ue_id);

CREATE INDEX IF NOT EXISTS idx_groupes_formation_id
    ON groupes(formation_id);

CREATE INDEX IF NOT EXISTS idx_etudiants_groupe_id
    ON etudiants(groupe_id);

CREATE INDEX IF NOT EXISTS idx_emplois_temps_groupe_id
    ON emplois_temps(groupe_id);

CREATE INDEX IF NOT EXISTS idx_emplois_temps_jour
    ON emplois_temps(jour);

CREATE INDEX IF NOT EXISTS idx_documents_type_document
    ON documents(type_document);

CREATE INDEX IF NOT EXISTS idx_documents_formation_id
    ON documents(formation_id);

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
    ON document_chunks(document_id);

CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
    ON document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMIT;
