"""Initial schema

Revision ID: 78fd0ec4fd63
Revises: 
Create Date: 2026-07-03 20:09:30.587574

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78fd0ec4fd63'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Consolidating all Synchropia tables."""
    
    # 1. Create 'organizations' table
    op.create_table(
        'organizations',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('github_org_name', sa.String(length=100), nullable=False),
        sa.Column('jira_project_key', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # 2. Create 'sessions' table
    op.create_table(
        'sessions',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('organization_id', sa.String(length=50), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    # Create indexes for fast joins
    op.create_index('idx_session_org', 'sessions', ['organization_id'], unique=False)
    op.create_index('idx_session_user', 'sessions', ['user_id'], unique=False)

    # 3. Create 'orchestration_runs' table
    op.create_table(
        'orchestration_runs',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('session_id', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('current_agent', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 4. Create 'agent_executions' table
    op.create_table(
        'agent_executions',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('run_id', sa.String(length=50), nullable=False),
        sa.Column('agent_name', sa.String(length=50), nullable=False),
        sa.Column('input_payload', sa.JSON(), nullable=False),
        sa.Column('output_payload', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['orchestration_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 5. Create 'tool_calls' table
    op.create_table(
        'tool_calls',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('agent_execution_id', sa.String(length=50), nullable=False),
        sa.Column('tool_name', sa.String(length=100), nullable=False),
        sa.Column('arguments', sa.JSON(), nullable=False),
        sa.Column('response_payload', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('execution_time_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agent_execution_id'], ['agent_executions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 6. Create 'artifacts' table
    op.create_table(
        'artifacts',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('run_id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('artifact_type', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['orchestration_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 7. Create 'message_logs' table
    op.create_table(
        'message_logs',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('session_id', sa.String(length=50), nullable=False),
        sa.Column('sender', sa.String(length=50), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # 8. Create 'code_quality_reports' table (Integrated)
    op.create_table(
        'code_quality_reports',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('run_id', sa.String(length=50), nullable=False),
        sa.Column('project_key', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('bugs', sa.Integer(), nullable=True),
        sa.Column('vulnerabilities', sa.Integer(), nullable=True),
        sa.Column('code_smells', sa.Integer(), nullable=True),
        sa.Column('coverage', sa.Float(), nullable=True),
        sa.Column('duplicated_lines_density', sa.Float(), nullable=True),
        sa.Column('raw_measures', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['orchestration_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema - Dropping all tables in reverse structural order."""
    op.drop_table('code_quality_reports')
    op.drop_table('message_logs')
    op.drop_table('artifacts')
    op.drop_table('tool_calls')
    op.drop_table('agent_executions')
    op.drop_table('orchestration_runs')
    
    op.drop_index('idx_session_user', table_name='sessions')
    op.drop_index('idx_session_org', table_name='sessions')
    op.drop_table('sessions')
    
    op.drop_table('organizations')