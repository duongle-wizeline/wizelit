from sqlalchemy import Column, Text, Boolean, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from models.base import BaseModel, TimestampMixin


class Step(BaseModel):
    __tablename__ = 'steps'

    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    threadId = Column(UUID(as_uuid=True), ForeignKey('threads.id', ondelete='CASCADE'), nullable=False)
    parentId = Column(UUID(as_uuid=True))
    streaming = Column(Boolean, nullable=False, default=False)
    waitForAnswer = Column(Boolean, default=False)
    isError = Column(Boolean, default=False)
    defaultOpen = Column(Boolean, default=False)
    meta_data = Column('metadata', JSONB, default=dict)
    tags = Column(ARRAY(Text), default=list)
    input = Column(Text)
    output = Column(Text)
    createdAt = Column(Text, default=TimestampMixin.get_timestamp)
    command = Column(Text)
    start = Column(Text)
    end = Column(Text)
    generation = Column(JSONB, default=dict)
    showInput = Column(Text)
    language = Column(Text)
    indent = Column(Integer, default=0)

    # Relationships
    thread = relationship("Thread", back_populates="steps")
