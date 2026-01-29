from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from typing import Dict, Any

Base = declarative_base()

class TimestampMixin:
    """Mixin for models that need timestamp functionality."""

    @staticmethod
    def get_timestamp() -> str:
        return datetime.utcnow().isoformat()


class BaseModel(Base):
    """Abstract base model with common functionality."""
    __abstract__ = True

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
