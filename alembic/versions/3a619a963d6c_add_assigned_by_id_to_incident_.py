"""add_assigned_by_id_to_incident_assignments

Revision ID: 3a619a963d6c
Revises: 241a9a726f41
Create Date: 2026-07-04 17:19:48.406200

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a619a963d6c'
down_revision: Union[str, Sequence[str], None] = '241a9a726f41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='incident_assignments' AND column_name='assigned_by_id'
            ) THEN
                ALTER TABLE incident_assignments ADD COLUMN assigned_by_id UUID;
                ALTER TABLE incident_assignments ADD CONSTRAINT fk_incident_assignments_assigned_by FOREIGN KEY (assigned_by_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END
        $$;
    """)

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_incident_assignments_assigned_by', 'incident_assignments', type_='foreignkey')
    op.drop_column('incident_assignments', 'assigned_by_id')
