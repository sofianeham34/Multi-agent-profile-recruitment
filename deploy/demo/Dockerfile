# Image de la démo publique : API et interface dans un seul conteneur.
# Construite depuis la racine du dépôt (contexte « . »).
FROM python:3.11-slim

RUN useradd -m -u 1000 user
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user src/ ./src/
COPY --chown=user deploy/demo/start.sh .
RUN mkdir -p logs data/chromadb && chown -R user /app

USER user
ENV HOME=/home/user \
    LLM_PROVIDER=openrouter \
    DEMO_PUBLIQUE=1 \
    DEMO_MAX_RUNS_JOUR=3 \
    MAX_PROFILS_PARALLELES=6 \
    HITL_TIMEOUT=300 \
    API_URL=http://127.0.0.1:8000 \
    UI_HOST=0.0.0.0

# Modèle d'embeddings de la mémoire RAG téléchargé au build : démarrage plus rapide.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['init'])"

CMD ["bash", "start.sh"]
