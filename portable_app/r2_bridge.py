"""R2 para PDFs de contas EM ABERTO na VPS, sem outro banco.

Credenciais SOMENTE na VPS, em /opt/apps/contas-runtime/r2-agent.json.
Chaves do Cloudflare nunca sao enviadas ao navegador nem ao PC.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

CONFIG = Path(os.environ.get("CONTAS_R2_CONFIG", "/data/ControleContas/r2-agent.json"))
BUCKET_DEFAULT = "controle-contas-pdfs"
MAX_PDF = 30 * 1024 * 1024
HEAD_CACHE = {}
CACHE_LOCK = threading.Lock()


class R2Unavailable(RuntimeError):
    pass


def _config():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    account = str(cfg.get("account_id", "")).strip().lower()
    key = str(cfg.get("access_key_id", "")).strip()
    secret = str(cfg.get("secret_access_key", "")).strip()
    token = str(cfg.get("agent_token", "")).strip()
    bucket = str(cfg.get("bucket", BUCKET_DEFAULT))
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        return None
    if not key or not secret or len(token) < 32:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", bucket):
        return None
    return {
        "account": account, "key": key, "secret": secret,
        "agent_token": token, "bucket": bucket,
    }


def enabled() -> bool:
    return _config() is not None


def verify_agent_token(received: str) -> bool:
    cfg = _config()
    return bool(cfg and hmac.compare_digest(cfg["agent_token"], str(received or "")))


@lru_cache(maxsize=1)
def _boto3():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise R2Unavailable("Biblioteca R2 nao instalada na VPS.") from exc
    return boto3, Config


def _client(cfg):
    boto3, Config = _boto3()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{cfg['account']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["key"],
        aws_secret_access_key=cfg["secret"],
        region_name="auto",
        config=Config(signature_version="s3v4", connect_timeout=8,
                      read_timeout=18, retries={"max_attempts": 2}),
    )


def _key(account_id: int):
    if not isinstance(account_id, int) or not 0 < account_id < 2**31:
        raise ValueError("ID da conta invalido.")
    return f"accounts/{account_id}/original.pdf"


def invalidate(account_id: int):
    with CACHE_LOCK:
        HEAD_CACHE.pop(account_id, None)


def metadata(account_id: int, *, fresh=False):
    cfg = _config()
    if cfg is None:
        return None
    now = time.monotonic()
    with CACHE_LOCK:
        hit = HEAD_CACHE.get(account_id)
        if hit and not fresh and now - hit[0] < 90:
            return hit[1]
    try:
        data = _client(cfg).head_object(Bucket=cfg["bucket"], Key=_key(account_id))
        meta = {
            "hash": str(data.get("Metadata", {}).get("sha256") or ""),
            "size": int(data.get("ContentLength", 0)),
            "pages": int(data.get("Metadata", {}).get("pages") or 0),
        }
    except Exception as exc:
        status = getattr(getattr(exc, "response", {}).get("ResponseMetadata", {}), "get", lambda *_: None)("HTTPStatusCode")
        # boto3 ClientError response e dict; erros de credencial/rede nao
        # podem ser interpretados como objeto inexistente.
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if status == 404 or code in {"NoSuchKey", "404", "NotFound"}:
            meta = None
        else:
            raise R2Unavailable("Nao foi possivel consultar o R2.") from exc
    with CACHE_LOCK:
        HEAD_CACHE[account_id] = (now, meta)
    return meta


def available(account_id: int) -> bool:
    try:
        return bool(metadata(account_id))
    except R2Unavailable:
        return False


def read_pdf(account_id: int) -> bytes:
    cfg = _config()
    if cfg is None:
        raise R2Unavailable("R2 ainda nao configurado na VPS.")
    try:
        obj = _client(cfg).get_object(Bucket=cfg["bucket"], Key=_key(account_id))
        if int(obj.get("ContentLength", 0)) > MAX_PDF:
            raise R2Unavailable("PDF excede 30 MB.")
        content = obj["Body"].read(MAX_PDF + 1)
        if not content.startswith(b"%PDF-") or len(content) > MAX_PDF:
            raise R2Unavailable("PDF remoto invalido.")
        declared = obj.get("Metadata", {}).get("sha256")
        if declared and hashlib.sha256(content).hexdigest() != declared:
            raise R2Unavailable("PDF remoto nao passou na integridade SHA256.")
        return content
    except R2Unavailable:
        raise
    except Exception as exc:
        raise R2Unavailable("Nao foi possivel abrir o PDF no R2.") from exc


def write_pdf(account_id: int, content: bytes, pdf_pages: int = 0):
    cfg = _config()
    if cfg is None:
        raise R2Unavailable("R2 ainda nao configurado na VPS.")
    if not 5 <= len(content) <= MAX_PDF or not content.startswith(b"%PDF-"):
        raise ValueError("Arquivo PDF invalido ou maior que 30 MB.")
    digest = hashlib.sha256(content).hexdigest()
    try:
        _client(cfg).put_object(
            Bucket=cfg["bucket"], Key=_key(account_id), Body=content,
            ContentType="application/pdf",
            Metadata={"sha256": digest, "pages": str(max(0, int(pdf_pages)))},
        )
    except Exception as exc:
        raise R2Unavailable("Falha ao enviar PDF ao R2.") from exc
    invalidate(account_id)
    return {"hash": digest, "size": len(content)}


def remove_confirmed_paid(account_id: int):
    """Apenas chamada apos comprovacao local Windows do PDF final."""
    cfg = _config()
    if cfg is None:
        raise R2Unavailable("R2 ainda nao configurado.")
    try:
        _client(cfg).delete_object(Bucket=cfg["bucket"], Key=_key(account_id))
    except Exception as exc:
        raise R2Unavailable("Falha ao excluir PDF pago do R2.") from exc
    invalidate(account_id)


@contextmanager
def temp_pdf(content: bytes):
    fd, location = tempfile.mkstemp(suffix=".pdf", prefix="contas-r2-")
    path = Path(location)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        yield path
    finally:
        path.unlink(missing_ok=True)
