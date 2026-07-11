-- Lightweight, append-only notes attached to coordination cases.
CREATE TABLE IF NOT EXISTS shared.case_notes (
    note_id         UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_token     UUID            NOT NULL
                                      REFERENCES shared.coordination_alerts(alert_token)
                                      ON DELETE CASCADE,
    author_role     TEXT            NOT NULL CHECK (btrim(author_role) <> ''),
    note_text       TEXT            NOT NULL CHECK (btrim(note_text) <> ''),
    noted_at        TIMESTAMPTZ     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_case_notes_alert_time
    ON shared.case_notes (alert_token, noted_at, created_at);

REVOKE ALL PRIVILEGES ON shared.case_notes FROM app_shared;
GRANT SELECT, INSERT ON shared.case_notes TO app_shared;
