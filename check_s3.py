"""Diagnostic S3 en lecture seule, sans AWS CLI et sans afficher de secrets."""
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import config


def main():
    print(f"Bucket : {config.BUCKET} | Région : {config.AWS_REGION}")
    print(f"Simulation de l'application : {config.SIMULATE_S3}")
    try:
        key = config.AWS_ACCESS_KEY_ID
        secret = config.AWS_SECRET_ACCESS_KEY
        token = config.AWS_SESSION_TOKEN
        if key or secret or token:
            if not key or not secret:
                print("ERREUR : renseigner les deux clés ensemble dans config.py.")
                return 1
            session = boto3.Session(aws_access_key_id=key,
                                    aws_secret_access_key=secret, aws_session_token=token)
        else:
            session = boto3.Session(profile_name=config.AWS_PROFILE)
        client = session.client('s3', region_name=config.AWS_REGION,
                                config=Config(connect_timeout=5, read_timeout=5,
                                              retries={'max_attempts': 0}))
        # Même requête que l'interface, limitée à dix entrées pour le diagnostic.
        response = client.list_objects_v2(Bucket=config.BUCKET, Prefix='',
                                          Delimiter='/', MaxKeys=10)
        print("Lecture S3 : OK")
        print("Fichiers à la racine (sur cette page) :", len([
            obj for obj in response.get('Contents', []) if not obj['Key'].endswith('/')]))
        print("Dossiers à la racine (sur cette page) :", len(response.get('CommonPrefixes', [])))
        print("Autres pages disponibles :", response.get('IsTruncated', False))
        print("Si l'interface diffère, redémarrer Python puis recharger la page du navigateur.")
        return 0
    except ClientError as exc:
        print("ERREUR AWS :", exc.response.get('Error', {}).get('Code', 'Unknown'))
    except BotoCoreError as exc:
        print("ERREUR de connexion/configuration :", type(exc).__name__)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
