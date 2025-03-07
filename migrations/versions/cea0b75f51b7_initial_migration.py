"""Initial migration

Revision ID: cea0b75f51b7
Revises: 
Create Date: 2025-03-03 04:14:20.743452

"""
from alembic import op
import sqlalchemy as sa
from models import SafeJSONList



# revision identifiers, used by Alembic.
revision = 'cea0b75f51b7'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('employees',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('shift_type', sa.String(length=10), nullable=False),
    sa.Column('preferred_day_off', SafeJSONList(), nullable=True),
    sa.Column('manual_days_off', SafeJSONList(), nullable=True),
    sa.Column('shift_requests', SafeJSONList(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('employees')
    # ### end Alembic commands ###
