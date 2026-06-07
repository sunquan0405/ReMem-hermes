# Embedding API Connectivity Test

Write a Python script `scripts/test_embedding_api.py` that tests the embedding API configuration and reports any misconfigurations.

## Checks to perform

### 1. Read config
- Read EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_BASE_URL from environment
- Report what's configured

### 2. Validate model name
- Check if EMBEDDING_MODEL starts with "deepseek" → WARN: DeepSeek has NO embedding models
- This is a common pitfall: deepseek-v4-flash is a CHAT model, cannot produce embeddings
- Correct models: text-embedding-v4 (Qwen, 1024-dim) or qwen/qwen3-embedding-8b (OpenRouter, 4096-dim)

### 3. Test API call
- Call {EMBEDDING_BASE_URL}/embeddings with model={EMBEDDING_MODEL}
- Input: "test embedding connectivity"
- Report HTTP status, response time, and embedding dimensions

### 4. Compare with Qdrant
- Query Qdrant collection /collections/knowledge_base for vector config
- Compare API output dimensions with Qdrant collection dimensions
- Warn if mismatch (e.g. 1024-dim API vs 4096-dim collection)

### Output format
```
=== Embedding API Test ===
Model:    text-embedding-v4
Base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
Status:   ✓ Connected (200, 234ms)
Dims:     1024
Qdrant:   1024-dim collection → ✓ Match
Verdict:  ✓ CONFIGURATION CORRECT
```

Or for error cases:
```
=== Embedding API Test ===
Model:    deepseek-v4-flash
Base URL: https://api.deepseek.com/v1
Status:   ✗ FAILED (400 Bad Request)
Dims:     N/A
Qdrant:   1024-dim collection
⚠ WARNING: deepseek-v4-flash is a chat model, not an embedding model!
Verdict:  ✗ MISCONFIGURED — fix EMBEDDING_MODEL in ~/.hermes/.env
```

### Dependencies
- stdlib only: os, subprocess, json, urllib.request, time
- No pip installs
