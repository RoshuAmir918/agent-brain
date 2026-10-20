"""Digest display names, resolved once when this module is imported."""
import os

HUMAN_NAME = os.environ.get("BRAIN_HUMAN_NAME", "Human")
AGENT_NAME = os.environ.get("BRAIN_AGENT_NAME", "Agent")
