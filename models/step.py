from sqlalchemy import Column, Text, Boolean, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from models.base import BaseModel


class Step(BaseModel):
    __tablename__ = 'steps'

    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    threadId = Column(UUID(as_uuid=True), ForeignKey('threads.id', ondelete='CASCADE'), nullable=False)
    parentId = Column(UUID(as_uuid=True))
    streaming = Column(Boolean, nullable=False)
    waitForAnswer = Column(Boolean)
    isError = Column(Boolean)
    defaultOpen = Column(Boolean)
    meta_data = Column('metadata', JSONB)
    tags = Column(ARRAY(Text))
    input = Column(Text)
    output = Column(Text)
    createdAt = Column(Text)
    command = Column(Text)
    start = Column(Text)
    end = Column(Text)
    generation = Column(JSONB)
    showInput = Column(Text)
    language = Column(Text)
    indent = Column(Integer)

    # Relationships
    thread = relationship("Thread", back_populates="steps")
