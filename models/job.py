"""
Job and JobLog models for persistent storage of job execution data.
"""
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from models.base import BaseModel


class JobStatus(str, enum.Enum):
    """Enumeration of possible job statuses."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobModel(BaseModel):
    """
    Persistent storage for job execution data.
    Tracks status, results, and errors for long-running jobs.
    """
    __tablename__ = "jobs"

    id = Column(String(64), primary_key=True)  # JOB-xxxxx
    status = Column(String(20), default=JobStatus.RUNNING.value, nullable=False)
    result = Column(JSONB, nullable=True)  # JSON result for completed jobs
    error = Column(Text, nullable=True)  # Error message for failed jobs
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship to logs
    logs = relationship("JobLogModel", back_populates="job", cascade="all, delete-orphan")

    # Index for faster queries
    __table_args__ = (
        Index('idx_job_status', 'status'),
        Index('idx_job_created_at', 'created_at'),
    )

    def __repr__(self):
        return f"<JobModel(id={self.id}, status={self.status})>"


class JobLogModel(BaseModel):
    """
    Persistent storage for individual job log entries.
    Captures timestamped log messages with severity levels.
    """
    __tablename__ = "job_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=False)
    level = Column(String(20), nullable=False)  # INFO, ERROR, WARNING, DEBUG
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship to job
    job = relationship("JobModel", back_populates="logs")

    # Indexes for faster queries
    __table_args__ = (
        Index('idx_job_log_job_id', 'job_id'),
        Index('idx_job_log_timestamp', 'timestamp'),
        Index('idx_job_log_job_id_timestamp', 'job_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<JobLogModel(id={self.id}, job_id={self.job_id}, level={self.level})>"
