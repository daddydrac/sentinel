"""SageMaker streaming inference container for a pinned public Qwen model."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Iterator

import torch
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer


MODEL_ID = os.environ["MODEL_ID"]
MODEL_REVISION = os.environ["MODEL_REVISION"]
MAX_NEW_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "768"))
if not MODEL_REVISION or MODEL_REVISION == "main":
    raise RuntimeError("MODEL_REVISION must be an immutable Hugging Face commit SHA")

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    revision=MODEL_REVISION,
    torch_dtype="auto",
    device_map="auto",
    low_cpu_mem_usage=True,
)
model.eval()

app = FastAPI()


@app.get("/ping")
def ping() -> JSONResponse:
    if model is None or tokenizer is None or not torch.cuda.is_available():
        return JSONResponse(status_code=503, content={"ready": False})
    return JSONResponse(status_code=200, content={"ready": True, "model": MODEL_ID})


def _validated(payload: dict) -> tuple[list, int]:
    """Check the request contract eagerly.

    A generator body does not execute until the response is already streaming,
    by which point the 200 status line has been sent. Validating here means a
    contract failure returns 400/413 instead of an empty 200 that callers
    cannot distinguish from a model failure.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages must be a non-empty array")
    if len(json.dumps(messages)) > 128_000:
        raise HTTPException(status_code=413, detail="messages exceed the request contract")
    try:
        max_new_tokens = int(payload.get("max_new_tokens", 512))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="max_new_tokens must be an integer") from exc
    if max_new_tokens < 1 or max_new_tokens > MAX_NEW_TOKENS:
        raise HTTPException(status_code=400, detail="max_new_tokens exceeds the endpoint contract")
    return messages, max_new_tokens


def _stream(messages: list, max_new_tokens: int) -> Iterator[bytes]:
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        enable_thinking=False,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    ).to(model.device)
    streamer = TextIteratorStreamer(
        tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=60.0
    )
    generation = {
        **inputs,
        "streamer": streamer,
        "max_new_tokens": max_new_tokens,
        "do_sample": False,
        "use_cache": True,
    }
    worker = threading.Thread(target=model.generate, kwargs=generation, daemon=True)
    worker.start()
    for text in streamer:
        if text:
            yield text.encode("utf-8")
    worker.join(timeout=5)
    if worker.is_alive():
        raise RuntimeError("Generation worker failed to stop")


@app.post("/invocations")
async def invocations(request: Request) -> StreamingResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    messages, max_new_tokens = _validated(payload)
    return StreamingResponse(
        _stream(messages, max_new_tokens), media_type="text/plain; charset=utf-8"
    )


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] != "serve":
        raise SystemExit("SageMaker must start this image with the 'serve' argument")
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1)


if __name__ == "__main__":
    main()

