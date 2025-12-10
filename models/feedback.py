from sqlalchemy import Column, Text, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from models.base import BaseModel


class Feedback(BaseModel):
    __tablename__ = 'feedbacks'

    forId = Column(UUID(as_uuid=True), nullable=False)
    threadId = Column(UUID(as_uuid=True), ForeignKey('threads.id', ondelete='CASCADE'), nullable=False)
    value = Column(Integer, nullable=False)
    comment = Column(Text)

    # Relationships
    thread = relationship("Thread", back_populates="feedbacks")
