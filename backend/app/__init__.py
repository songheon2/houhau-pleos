"""Backend package. backend/.env is loaded first so settings read at import time see it."""
from .env import load_env_file

load_env_file()
