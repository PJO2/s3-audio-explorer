"""Accès S3 en lecture seule et simulateur déterministe sans AWS."""
import io
import math
import struct
import wave
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock


# Dimensions publiées pour BucketSizeBytes, y compris les surcoûts de stockage.
STORAGE_TYPES = (
    'StandardStorage', 'ReducedRedundancyStorage', 'StandardIAStorage',
    'StandardIAObjectOverhead', 'StandardIASizeOverhead', 'OneZoneIAStorage',
    'OneZoneIASizeOverhead', 'IntelligentTieringFAStorage', 'IntelligentTieringIAStorage',
    'IntelligentTieringAAStorage', 'IntelligentTieringAIAStorage',
    'IntelligentTieringDAAStorage',
    'GlacierStorage', 'GlacierObjectOverhead', 'GlacierS3ObjectOverhead',
    'GlacierStagingStorage', 'GlacierInstantRetrievalStorage',
    'GlacierInstantRetrievalSizeOverhead', 'DeepArchiveStorage',
    'DeepArchiveObjectOverhead', 'DeepArchiveS3ObjectOverhead',
    'DeepArchiveStagingStorage', 'ExpressOneZoneStorage',
)


class S3Storage:
    def __init__(self, bucket, region, profile=None, *, access_key=None, secret_key=None,
                 session_token=None):
        self.bucket = bucket
        self.region = region
        self.profile = profile
        self._access_key = access_key
        self._secret_key = secret_key
        self._session_token = session_token
        self.client = None
        self.cloudwatch_client = None
        self._session = None
        self._client_lock = Lock()
        self._size_lock = Lock()
        self._size_cache = None
        self._size_cached_at = 0

    def _get_client(self, service="s3"):
        # Reporter l'authentification à la première requête permet à l'interface
        # d'afficher une erreur de configuration sans empêcher le démarrage.
        with self._client_lock:
            attribute = 'client' if service == 's3' else 'cloudwatch_client'
            if getattr(self, attribute) is not None:
                return getattr(self, attribute)
            if self._session is None:
                import boto3
                if self._access_key or self._secret_key or self._session_token:
                    if not self._access_key or not self._secret_key:
                        raise ValueError("Renseigner ensemble AWS_ACCESS_KEY_ID et AWS_SECRET_ACCESS_KEY.")
                    session = boto3.Session(
                        aws_access_key_id=self._access_key,
                        aws_secret_access_key=self._secret_key,
                        aws_session_token=self._session_token)
                else:
                    session = boto3.Session(profile_name=self.profile)
                self._session = session
            client = self._session.client(service, region_name=self.region)
            setattr(self, attribute, client)
            return client

    def bucket_size(self):
        with self._size_lock:
            if self._size_cache is not None and time.monotonic() - self._size_cached_at < 3600:
                return self._size_cache
            client = self._get_client('cloudwatch')
            end = datetime.now(timezone.utc)
            start = (end - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

            def read(storage_type):
                return client.get_metric_statistics(
                    Namespace='AWS/S3', MetricName='BucketSizeBytes',
                    Dimensions=[{'Name': 'BucketName', 'Value': self.bucket},
                                {'Name': 'StorageType', 'Value': storage_type}],
                    StartTime=start, EndTime=end, Period=86400,
                    Statistics=['Average'], Unit='Bytes')['Datapoints']

            totals = {}
            with ThreadPoolExecutor(max_workers=6) as executor:
                for points in executor.map(read, STORAGE_TYPES):
                    for point in points:
                        day = point['Timestamp'].astimezone(timezone.utc).date().isoformat()
                        totals[day] = totals.get(day, 0) + point['Average']
            day = max(totals) if totals else None
            result = {'size': round(totals[day]) if day else None, 'date': day}
            # Une mesure peut être publiée juste après cette lecture : permettre
            # un nouvel essai au rechargement lorsqu'aucune donnée n'existe.
            if day is not None:
                self._size_cache = result
                self._size_cached_at = time.monotonic()
            return result

    def list(self, prefix):
        folders, files = set(), []
        pages = self._get_client().get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix, Delimiter="/"
        )
        for page in pages:
            folders.update(item["Prefix"] for item in page.get("CommonPrefixes", []))
            files.extend({"key": item["Key"], "size": item["Size"]}
                         for item in page.get("Contents", []) if not item["Key"].endswith("/"))
        return sorted(folders), sorted(files, key=lambda item: item["key"])

    def open(self, key):
        response = self._get_client().get_object(Bucket=self.bucket, Key=key)
        return response["Body"], response["ContentLength"]


class SimulatedStorage:
    def __init__(self):
        # Véritables fichiers WAV : les téléchargements sont aussi écoutables.
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(22050)
            samples = b"".join(struct.pack("<h", int(2000 * math.sin(2 * math.pi * 440 * n / 22050)))
                               for n in range(22050))
            output.writeframes(samples * 60)
        data = buffer.getvalue()
        self.objects = {key: data for key in [
            "Bienvenue.wav", "Musique/Jazz/Piano.wav", "Musique/Jazz/Contrebasse.wav",
            "Musique/Ambient/Pluie.wav", "Musique/Ambient/Vent.wav",
            "Podcasts/Épisode 01.wav", "Podcasts/Épisode 02.wav",
        ]}
        self.objects["Musique/notes.txt"] = b"Document non audio"

    def list(self, prefix):
        folders, files = set(), []
        for key, data in sorted(self.objects.items()):
            if not key.startswith(prefix):
                continue
            tail = key[len(prefix):]
            if "/" in tail:
                folders.add(prefix + tail.split("/", 1)[0] + "/")
            elif tail:
                files.append({"key": key, "size": len(data)})
        return sorted(folders), files

    def bucket_size(self):
        return {'size': sum(len(data) for data in self.objects.values()),
                'date': datetime.now(timezone.utc).date().isoformat()}

    def open(self, key):
        data = self.objects[key]
        return io.BytesIO(data), len(data)
