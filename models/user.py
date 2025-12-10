from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from models.base import BaseModel

class User(BaseModel):
    __tablename__ = 'users'

    identifier = Column(Text, nullable=False, unique=True)
    meta_data = Column('metadata', JSONB, nullable=False)
    createdAt = Column(Text)

    # Relationships
    threads = relationship("Thread", back_populates="user", cascade="all, delete-orphan")
