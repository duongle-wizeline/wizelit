from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from models.base import BaseModel, TimestampMixin

class User(BaseModel):
    __tablename__ = 'users'

    identifier = Column(Text, nullable=False, unique=True)
    meta_data = Column('metadata', JSONB, nullable=False, default=dict)
    createdAt = Column(Text, default=TimestampMixin.get_timestamp)

    # Relationships
    threads = relationship("Thread", back_populates="user", cascade="all, delete-orphan")
