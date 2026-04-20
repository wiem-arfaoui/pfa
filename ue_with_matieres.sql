CREATE OR REPLACE VIEW vue_unites_avec_matieres AS
SELECT
    ue.id AS ue_id,
    ue.semestre_id,
    ue.code_ue,
    ue.nom_ue,
    ue.coefficient_ue,
    ue.credits_ue,
    COUNT(m.id) AS nombre_matieres,
    STRING_AGG(m.nom, ' | ' ORDER BY m.id) AS matieres
FROM unites_enseignement ue
LEFT JOIN matieres m ON m.ue_id = ue.id
GROUP BY
    ue.id,
    ue.semestre_id,
    ue.code_ue,
    ue.nom_ue,
    ue.coefficient_ue,
    ue.credits_ue;
