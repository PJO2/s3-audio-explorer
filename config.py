"""Configuration locale : modifier ces valeurs avant de démarrer."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BUCKET = "aws-ff8000-mys3"
DESTINATION = BUCKET
SIMULATE_S3 = False
AWS_REGION = "eu-north-1"
# None utilise le profil par défaut ou AWS_PROFILE ; sinon indiquer un profil nommé.
# Facultatif : clés IAM en lecture seule, sans AWS CLI. Garder ce fichier privé.
# Laisser None pour utiliser le profil ou les identifiants de l'environnement.
HOST = "127.0.0.1"
PORT = 8080
MAX_DOWNLOADS = 3
CHUNK_SIZE = 256 * 1024
SIMULATION_DELAY = 0.08

# Identifiants privés dans ~/.aws/credentials (sans AWS CLI).
AWS_PROFILE = "s3-audio-reader"
AWS_ACCESS_KEY_ID = None
AWS_SECRET_ACCESS_KEY = None
AWS_SESSION_TOKEN = None
