"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('username', sa.String(length=32), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('workspaces',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('channels',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('type', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'name')
    )
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_channels_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('workspace_members',
    sa.Column('workspace_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'user_id')
    )
    op.create_table('channel_members',
    sa.Column('channel_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('channel_id', 'user_id')
    )
    op.create_table('messages',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('channel_id', sa.String(length=32), nullable=False),
    sa.Column('author_id', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('reply_to', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reply_to'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index('ix_messages_channel_created', ['channel_id', 'created_at', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_reply_to'), ['reply_to'], unique=False)

    op.create_table('message_reactions',
    sa.Column('message_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('reaction', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('message_id', 'user_id', 'reaction')
    )
    op.create_table('notifications',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('message_id', sa.String(length=32), nullable=True),
    sa.Column('channel_id', sa.String(length=32), nullable=True),
    sa.Column('text', sa.String(length=300), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))

    op.drop_table('notifications')
    op.drop_table('message_reactions')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_reply_to'))
        batch_op.drop_index('ix_messages_channel_created')

    op.drop_table('messages')
    op.drop_table('channel_members')
    op.drop_table('workspace_members')
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_channels_workspace_id'))

    op.drop_table('channels')
    op.drop_table('workspaces')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
