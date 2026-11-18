"""Les tests n'écrivent jamais dans les données réelles (historique, mémoire RAG, journaux)."""

import os
import tempfile

# Fixé avant l'import des modules du projet, qui lisent ces chemins au chargement.
_TMP = tempfile.mkdtemp(prefix="sma-tests-")
os.environ["RUNS_DB_PATH"] = os.path.join(_TMP, "runs.sqlite")
os.environ["CHROMADB_DIR"] = os.path.join(_TMP, "chromadb")
os.environ["METRICS_DIR"] = os.path.join(_TMP, "logs")
