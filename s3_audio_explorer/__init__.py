import atexit
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError, PartialCredentialsError, ProfileNotFound
from flask import Flask, jsonify, render_template, request
from .storage import S3Storage, SimulatedStorage
from .transfers import TransferManager


def create_app(overrides=None, storage=None):
    app = Flask(__name__)
    app.config.from_object("config")
    if overrides:
        app.config.update(overrides)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    storage = storage if storage is not None else (
        SimulatedStorage() if app.config["SIMULATE_S3"] else
        S3Storage(app.config["BUCKET"], app.config["AWS_REGION"], app.config["AWS_PROFILE"],
                  access_key=app.config.get("AWS_ACCESS_KEY_ID"),
                  secret_key=app.config.get("AWS_SECRET_ACCESS_KEY"),
                  session_token=app.config.get("AWS_SESSION_TOKEN")))
    manager = TransferManager(storage, app.config["DESTINATION"],
                              workers=min(3, max(1, app.config["MAX_DOWNLOADS"])),
                              chunk_size=app.config["CHUNK_SIZE"],
                              delay=app.config["SIMULATION_DELAY"] if app.config["SIMULATE_S3"] else 0)
    app.extensions["transfers"] = manager
    atexit.register(manager.shutdown)

    @app.get("/")
    def index():
        return render_template("index.html", bucket=app.config["BUCKET"],
                               simulation=app.config["SIMULATE_S3"], destination=str(manager.destination))

    @app.get("/api/browse")
    def browse():
        prefix = request.args.get("prefix", "")
        if prefix and not prefix.endswith("/"):
            return jsonify(error="Un préfixe doit se terminer par /"), 400
        try:
            folders, files = storage.list(prefix)
            return jsonify(prefix=prefix, folders=folders, files=files)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            messages = {
                "AccessDenied": "Accès refusé : vérifiez la permission s3:ListBucket et la politique du bucket.",
                "InvalidAccessKeyId": "AWS ne reconnaît pas la clé d'accès configurée.",
                "SignatureDoesNotMatch": "Signature AWS incorrecte : vérifiez la clé secrète et l'horloge du serveur.",
                "ExpiredToken": "Les identifiants temporaires AWS ont expiré.",
                "InvalidToken": "Le jeton de session AWS est invalide.",
                "NoSuchBucket": "Le bucket configuré n'existe pas.",
                "AuthorizationHeaderMalformed": "Vérifiez la région AWS configurée pour ce bucket.",
            }
            message = messages.get(code, "AWS a refusé la lecture du bucket.")
            return jsonify(error=f"{message} (AWS : {code})"), 502
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound):
            return jsonify(error="Identifiants AWS absents ou profil introuvable. Vérifiez config.py puis redémarrez le serveur."), 502
        except EndpointConnectionError:
            return jsonify(error="Connexion à S3 impossible. Vérifiez le réseau du serveur et la région AWS."), 502
        except Exception:
            app.logger.exception("Échec de la lecture S3")
            return jsonify(error="Impossible de lire S3. Vérifiez le bucket et les identifiants AWS."), 502

    @app.get("/api/bucket-size")
    def bucket_size():
        try:
            return jsonify(storage.bucket_size())
        except ClientError as exc:
            code = exc.response.get('Error', {}).get('Code', 'Unknown')
            if code in ('AccessDenied', 'AccessDeniedException'):
                message = 'Accès CloudWatch refusé : vérifiez la permission cloudwatch:GetMetricStatistics.'
            else:
                message = 'Impossible de lire la taille dans CloudWatch.'
            return jsonify(error=f'{message} (AWS : {code})'), 502
        except (NoCredentialsError, PartialCredentialsError, ProfileNotFound):
            return jsonify(error='Identifiants AWS absents ou profil introuvable.'), 502
        except Exception:
            app.logger.exception('Échec de la lecture de la taille CloudWatch')
            return jsonify(error='Taille indisponible : connexion à CloudWatch impossible.'), 502

    @app.get("/api/transfers")
    def transfers():
        return jsonify(transfers=manager.snapshot())

    @app.post("/api/transfers")
    def enqueue():
        data = request.get_json(silent=True)
        keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 1000 or any(
            not isinstance(k, str) for k in keys
        ):
            return jsonify(error="Sélectionnez entre 1 et 1000 fichiers."), 400
        try:
            return jsonify(ids=manager.enqueue(keys)), 202
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/transfers/<job_id>/cancel")
    def cancel(job_id):
        if not manager.cancel(job_id):
            return jsonify(error="Transfert introuvable"), 404
        return jsonify(ok=True)

    return app
