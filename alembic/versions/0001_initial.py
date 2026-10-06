"""Initial private schema; frozen DDL, never imported from live models."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA taskflow")
    op.execute("REVOKE ALL ON SCHEMA taskflow FROM PUBLIC")
    op.execute(
        "\nCREATE TABLE taskflow.dialogs (\n\tkey VARCHAR(100) NOT NULL, \n\tstate VARCHAR(100), \n\tdata JSONB NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (key)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE taskflow.penalty_types (\n\tid SERIAL NOT NULL, \n\tseed_key VARCHAR(32), \n\tname VARCHAR(80) NOT NULL, \n\temoji VARCHAR(16) NOT NULL, \n\tdescription VARCHAR(500) NOT NULL, \n\tcalculation_type VARCHAR(20) NOT NULL, \n\trate INTEGER NOT NULL, \n\tis_active BOOLEAN NOT NULL, \n\tsort_order INTEGER NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (calculation_type IN ('PER_MINUTE','FIXED','MANUAL')), \n\tCHECK (rate BETWEEN 0 AND 1000000), \n\tCHECK (calculation_type = 'MANUAL' OR rate > 0), \n\tUNIQUE (seed_key)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE taskflow.telegram_updates (\n\tupdate_id BIGSERIAL NOT NULL, \n\tpayload JSONB NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tavailable_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (update_id)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_updates_pending ON taskflow.telegram_updates (status, available_at, update_id)"
    )
    op.execute(
        "\nCREATE TABLE taskflow.users (\n\tid SERIAL NOT NULL, \n\ttelegram_id BIGINT NOT NULL, \n\tdisplay_name VARCHAR(50) NOT NULL, \n\trole VARCHAR(20) NOT NULL, \n\ttimezone VARCHAR(64) NOT NULL, \n\tweekly_target INTEGER NOT NULL, \n\tlast_rating_notification_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (weekly_target > 0), \n\tUNIQUE (telegram_id), \n\tUNIQUE (role)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE taskflow.audit_events (\n\tid SERIAL NOT NULL, \n\tactor_user_id INTEGER NOT NULL, \n\tentity_type VARCHAR(30) NOT NULL, \n\tentity_id INTEGER NOT NULL, \n\taction VARCHAR(50) NOT NULL, \n\told_data JSONB NOT NULL, \n\tnew_data JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(actor_user_id) REFERENCES taskflow.users (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_audit_entity ON taskflow.audit_events (entity_type, entity_id, id)")
    op.execute(
        "\nCREATE TABLE taskflow.notifications (\n\tid SERIAL NOT NULL, \n\trecipient_user_id INTEGER NOT NULL, \n\tkind VARCHAR(30) NOT NULL, \n\tpayload JSONB NOT NULL, \n\tdedupe_key VARCHAR(100) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tavailable_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(recipient_user_id) REFERENCES taskflow.users (id), \n\tUNIQUE (dedupe_key)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_notifications_due ON taskflow.notifications (status, available_at)")
    op.execute(
        "CREATE UNIQUE INDEX uq_rating_pending ON taskflow.notifications (recipient_user_id) WHERE kind = 'rating_pending' AND status = 'pending'"
    )
    op.execute(
        "\nCREATE TABLE taskflow.penalties (\n\tid SERIAL NOT NULL, \n\ttarget_user_id INTEGER NOT NULL, \n\tcreated_by_user_id INTEGER NOT NULL, \n\tpenalty_type_id INTEGER NOT NULL, \n\tname_snapshot VARCHAR(80) NOT NULL, \n\tdescription_snapshot VARCHAR(500) NOT NULL, \n\tcalculation_type_snapshot VARCHAR(20) NOT NULL, \n\trate_snapshot INTEGER NOT NULL, \n\tquantity INTEGER, \n\tpoints INTEGER NOT NULL, \n\tcomment VARCHAR(1500), \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\toperation_key VARCHAR(80) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (points BETWEEN 1 AND 1000000000), \n\tCHECK (quantity IS NULL OR quantity BETWEEN 1 AND 10000), \n\tFOREIGN KEY(target_user_id) REFERENCES taskflow.users (id), \n\tFOREIGN KEY(created_by_user_id) REFERENCES taskflow.users (id), \n\tFOREIGN KEY(penalty_type_id) REFERENCES taskflow.penalty_types (id), \n\tUNIQUE (operation_key)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_penalties_target_created ON taskflow.penalties (target_user_id, created_at)"
    )
    op.execute(
        "\nCREATE TABLE taskflow.score_events (\n\tid SERIAL NOT NULL, \n\tuser_id INTEGER NOT NULL, \n\tcategory VARCHAR(10) NOT NULL, \n\tpoints INTEGER NOT NULL, \n\teffective_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\toperation_key VARCHAR(100) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (category IN ('task','penalty')), \n\tFOREIGN KEY(user_id) REFERENCES taskflow.users (id), \n\tUNIQUE (operation_key)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_score_user_effective ON taskflow.score_events (user_id, effective_at)"
    )
    op.execute(
        "\nCREATE TABLE taskflow.tasks (\n\tid SERIAL NOT NULL, \n\towner_user_id INTEGER NOT NULL, \n\tevaluator_user_id INTEGER NOT NULL, \n\ttitle VARCHAR(200) NOT NULL, \n\tcomment VARCHAR(1500), \n\tdeadline TIMESTAMP WITH TIME ZONE, \n\tstatus VARCHAR(20) NOT NULL, \n\trated_points INTEGER, \n\trated_by_user_id INTEGER, \n\trated_at TIMESTAMP WITH TIME ZONE, \n\tawarded_points INTEGER, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tcancelled_at TIMESTAMP WITH TIME ZONE, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\toperation_key VARCHAR(80) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCHECK (owner_user_id <> evaluator_user_id), \n\tCHECK (rated_points IS NULL OR rated_points BETWEEN 1 AND 10000), \n\tCHECK (awarded_points IS NULL OR awarded_points BETWEEN 1 AND 10000), \n\tCHECK (length(title) BETWEEN 1 AND 200), \n\tCHECK (status IN ('BACKLOG','TODO','IN_PROGRESS','DONE','CANCELLED')), \n\tCHECK (rated_by_user_id IS NULL OR rated_by_user_id = evaluator_user_id), \n\tCHECK (status <> 'DONE' OR (completed_at IS NOT NULL AND awarded_points IS NOT NULL)), \n\tFOREIGN KEY(owner_user_id) REFERENCES taskflow.users (id), \n\tFOREIGN KEY(evaluator_user_id) REFERENCES taskflow.users (id), \n\tFOREIGN KEY(rated_by_user_id) REFERENCES taskflow.users (id), \n\tUNIQUE (operation_key)\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_tasks_evaluator_status_id ON taskflow.tasks (evaluator_user_id, status, id)"
    )
    op.execute("CREATE INDEX ix_tasks_completed_at ON taskflow.tasks (completed_at)")
    op.execute(
        "CREATE INDEX ix_tasks_owner_status_id ON taskflow.tasks (owner_user_id, status, id)"
    )
    op.execute(
        "CREATE FUNCTION taskflow.reject_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Events are immutable'; END $$"
    )
    op.execute(
        "CREATE TRIGGER score_events_immutable BEFORE UPDATE OR DELETE ON taskflow.score_events FOR EACH ROW EXECUTE FUNCTION taskflow.reject_event_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON taskflow.audit_events FOR EACH ROW EXECUTE FUNCTION taskflow.reject_event_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Production initial migration cannot be destructively downgraded; restore a backup"
    )
