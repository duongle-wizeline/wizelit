from sqlalchemy import Column, Text, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from models.base import BaseModel


class Element(BaseModel):
    __tablename__ = 'elements'

    threadId = Column(UUID(as_uuid=True), ForeignKey('threads.id', ondelete='CASCADE'))
    type = Column(Text)
    url = Column(Text)
    chainlitKey = Column(Text)
    name = Column(Text, nullable=False)
    display = Column(Text)
    objectKey = Column(Text)
    size = Column(Text)
    page = Column(Integer)
    language = Column(Text)
    forId = Column(UUID(as_uuid=True))
    mime = Column(Text)
    props = Column(JSONB)

    # Relationships
    thread = relationship("Thread", back_populates="elements")
