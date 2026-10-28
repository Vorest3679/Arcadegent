"""Generic artifact model and sink."""

from app.agent.artifacts.model import Artifact, ArtifactScope
from app.agent.artifacts.sink import ArtifactSink

__all__ = ["Artifact", "ArtifactScope", "ArtifactSink"]
