from sqlalchemy import Column, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from models.base import BaseModel, TimestampMixin


class Thread(BaseModel):
    __tablename__ = 'threads'

    createdAt = Column(Text, default=TimestampMixin.get_timestamp)
    name = Column(Text)
    userId = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'))
    userIdentifier = Column(Text)
    tags = Column(ARRAY(Text), default=list)
    meta_data = Column('metadata', JSONB, default=dict)

    # Relationships
    user = relationship("User", back_populates="threads")
    steps = relationship("Step", back_populates="thread", cascade="all, delete-orphan")
    elements = relationship("Element", back_populates="thread", cascade="all, delete-orphan")
    feedbacks = relationship("Feedback", back_populates="thread", cascade="all, delete-orphan")
