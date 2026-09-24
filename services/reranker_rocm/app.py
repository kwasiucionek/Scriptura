"""Reranker na GPU (ROCm/CUDA/CPU) z API zgodnym z TEI: POST /rerank {query, texts} -> [{index, score}].

Do dema z maszyny z kartą (np. Ryzen AI z 96 GB dla iGPU przez ROCm), zamiast kontenera TEI na CPU.
Uruchomienie (w osobnym venv):
    pip install torch --index-url https://download.pytorch.org/whl/rocm6.2   # lub cuda / cpu
    pip install sentence-transformers fastapi uvicorn
    RERANK_MODEL=BAAI/bge-reranker-v2-m3 uvicorn app:app --host 127.0.0.1 --port 8081
W .env aplikacji: RERANKER_BACKEND=tei, TEI_RERANK_URL=http://127.0.0.1:8081
"""

import os

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
MAX_LENGTH = int(os.environ.get("RERANK_MAX_LENGTH", "1024"))
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"  # ROCm zgłasza się jako cuda

app = FastAPI(title="scriptura-reranker")
model = CrossEncoder(
    MODEL, max_length=MAX_LENGTH, device=DEVICE, trust_remote_code=True
)


class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    raw_scores: bool = False
    truncate: bool = True


@app.get("/health")
def health():
    return {"model": MODEL, "device": DEVICE}


@app.post("/rerank")
def rerank(req: RerankRequest):
    pairs = [(req.query, t) for t in req.texts]
    scores = model.predict(
        pairs, batch_size=32, activation_fct=None if req.raw_scores else torch.sigmoid
    )
    rows = [{"index": i, "score": float(s)} for i, s in enumerate(scores)]
    return sorted(rows, key=lambda r: -r["score"])
