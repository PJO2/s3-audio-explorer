"""File d'attente bornée par trois workers, progression et annulation."""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class Cancelled(Exception):
    pass


class TransferManager:
    def __init__(self, storage, destination, workers=3, chunk_size=262144, delay=0):
        self.storage = storage
        self.destination = Path(destination).resolve()
        self.chunk_size = chunk_size
        self.delay = delay
        self.lock = threading.RLock()
        self.jobs = {}
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="s3-download")

    def target(self, key):
        if not isinstance(key, str) or not key or "\\" in key or "\x00" in key:
            raise ValueError("Clé S3 invalide")
        parts = key.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Chemin S3 dangereux ou non représentable sur disque")
        target = self.destination.joinpath(*parts)
        if not target.resolve().is_relative_to(self.destination):
            raise ValueError("Le chemin sort du dossier de destination")
        return target

    def enqueue(self, keys):
        # Valider le lot entier avant d'ajouter le premier transfert.
        for key in keys:
            self.target(key)
        added = []
        with self.lock:
            for key in dict.fromkeys(keys):
                existing = next((j for j in self.jobs.values() if j["key"] == key
                                 and j["status"] in {"queued", "downloading", "cancelling"}), None)
                if existing:
                    added.append(existing["id"])
                    continue
                job_id = uuid.uuid4().hex
                job = dict(id=job_id, key=key, status="queued", downloaded=0,
                           total=0, error=None, event=threading.Event())
                self.jobs[job_id] = job
                job["future"] = self.executor.submit(self._download, job)
                added.append(job_id)
        return added

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return False
            if job["status"] in {"queued", "downloading"}:
                job["event"].set()
                if job["future"].cancel():
                    job["status"] = "cancelled"
                else:
                    job["status"] = "cancelling"
            return True

    def snapshot(self):
        with self.lock:
            return [{k: v for k, v in job.items() if k not in {"event", "future"}}
                    for job in self.jobs.values()]

    def _download(self, job):
        body, temporary = None, None
        try:
            with self.lock:
                if job["event"].is_set():
                    raise Cancelled()
                job["status"] = "downloading"
            target = self.target(job["key"])
            target.parent.mkdir(parents=True, exist_ok=True)
            self.target(job["key"])
            body, total = self.storage.open(job["key"])
            with self.lock:
                job["total"] = total
            temporary = target.with_name(f".{target.name}.{job['id']}.part")
            with temporary.open("xb") as output:
                while True:
                    if job["event"].is_set():
                        raise Cancelled()
                    chunk = body.read(self.chunk_size)
                    if not chunk:
                        break
                    output.write(chunk)
                    with self.lock:
                        job["downloaded"] += len(chunk)
                    if self.delay and job["event"].wait(self.delay):
                        raise Cancelled()
            with self.lock:
                if job["event"].is_set():
                    raise Cancelled()
                if job["downloaded"] != total:
                    raise IOError("Transfert incomplet : taille reçue incorrecte")
                self.target(job["key"])
                os.replace(temporary, target)
                job["status"] = "completed"
        except Cancelled:
            with self.lock:
                job["status"] = "cancelled"
        except Exception as exc:
            with self.lock:
                job["status"] = "cancelled" if job["event"].is_set() else "failed"
                job["error"] = str(exc)
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def shutdown(self):
        with self.lock:
            for job in self.jobs.values():
                if job["status"] in {"queued", "downloading", "cancelling"}:
                    self.cancel(job["id"])
        self.executor.shutdown(wait=True, cancel_futures=True)
