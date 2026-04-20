\echo ================================
\echo Verification des tables de pfa1
\echo ================================

\echo
\echo 1. Nombre de lignes par table
SELECT 'formations' AS table_name, COUNT(*) AS row_count FROM formations
UNION ALL
SELECT 'niveaux', COUNT(*) FROM niveaux
UNION ALL
SELECT 'semestres', COUNT(*) FROM semestres
UNION ALL
SELECT 'etudiants', COUNT(*) FROM etudiants
UNION ALL
SELECT 'unites_enseignement', COUNT(*) FROM unites_enseignement
UNION ALL
SELECT 'matieres', COUNT(*) FROM matieres
UNION ALL
SELECT 'groupes', COUNT(*) FROM groupes
UNION ALL
SELECT 'emplois_temps', COUNT(*) FROM emplois_temps
UNION ALL
SELECT 'evenements_calendrier', COUNT(*) FROM evenements_calendrier
UNION ALL
SELECT 'documents', COUNT(*) FROM documents
UNION ALL
SELECT 'document_chunks', COUNT(*) FROM document_chunks
ORDER BY table_name;

\echo
\echo 2. Apercu des formations
TABLE formations;

\echo
\echo 3. Apercu des niveaux
SELECT * FROM niveaux ORDER BY formation_id, annee LIMIT 20;

\echo
\echo 4. Apercu des semestres
SELECT * FROM semestres ORDER BY niveau_id, numero LIMIT 20;

\echo
\echo 5. Apercu des unites d enseignement
SELECT * FROM unites_enseignement ORDER BY semestre_id, id LIMIT 20;

\echo
\echo 6. Apercu des matieres
SELECT * FROM matieres ORDER BY ue_id, id LIMIT 30;

\echo
\echo 7. Apercu des groupes
SELECT * FROM groupes ORDER BY formation_id, annee, nom_groupe LIMIT 30;

\echo
\echo 8. Apercu des etudiants
SELECT * FROM etudiants ORDER BY groupe_id, nom_complet LIMIT 30;

\echo
\echo 9. Apercu des emplois du temps
SELECT * FROM emplois_temps ORDER BY groupe_id, jour, heure_debut LIMIT 30;

\echo
\echo 10. Apercu des evenements calendrier
SELECT * FROM evenements_calendrier ORDER BY date_debut NULLS LAST, id LIMIT 30;

\echo
\echo 11. Apercu des documents
SELECT
    id,
    type_document,
    titre,
    formation_id,
    niveau_id,
    semestre_id,
    source_path,
    LEFT(contenu_texte, 120) AS extrait
FROM documents
ORDER BY id
LIMIT 20;

\echo
\echo 12. Apercu des chunks avec verification embedding
SELECT
    id,
    document_id,
    chunk_index,
    LEFT(contenu, 120) AS extrait,
    vector_dims(embedding) AS embedding_dims
FROM document_chunks
ORDER BY document_id, chunk_index
LIMIT 20;

\echo
\echo 13. Verification rapide des relations
SELECT
    f.nom AS formation,
    n.annee,
    s.numero AS semestre
FROM semestres s
JOIN niveaux n ON n.id = s.niveau_id
JOIN formations f ON f.id = n.formation_id
ORDER BY f.nom, n.annee, s.numero
LIMIT 30;

\echo
\echo 14. Verification groupes et emplois
SELECT
    g.nom_groupe,
    g.annee,
    COUNT(et.id) AS nb_seances
FROM groupes g
LEFT JOIN emplois_temps et ON et.groupe_id = g.id
GROUP BY g.id, g.nom_groupe, g.annee
ORDER BY g.annee, g.nom_groupe
LIMIT 30;

\echo
\echo 15. Verification groupes et etudiants
SELECT
    g.nom_groupe,
    g.annee,
    COUNT(e.id) AS nb_etudiants
FROM groupes g
LEFT JOIN etudiants e ON e.groupe_id = g.id
GROUP BY g.id, g.nom_groupe, g.annee
ORDER BY g.annee, g.nom_groupe
LIMIT 30;

\echo
\echo Fin de verification
