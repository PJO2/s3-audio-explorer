"""Déplacer les clés de config.py vers ~/.aws/credentials, sans AWS CLI."""
import ast
import configparser
import io
import os
from pathlib import Path
import tempfile


PROFILE = 's3-audio-reader'
FIELDS = ('AWS_PROFILE', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.credentials-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate(config_path, credentials_path):
    if config_path.is_symlink() or credentials_path.is_symlink():
        raise ValueError('Refus de modifier un fichier lié symboliquement.')
    source = config_path.read_text(encoding='utf-8')
    assignments = []
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in FIELDS:
                values[target.id] = ast.literal_eval(node.value)
                assignments.append(node)
    key = values.get('AWS_ACCESS_KEY_ID')
    secret = values.get('AWS_SECRET_ACCESS_KEY')
    token = values.get('AWS_SESSION_TOKEN')
    if not key and not secret and not token:
        raise ValueError('Aucune clé à déplacer dans config.py.')
    if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret.strip():
        raise ValueError('Les deux clés doivent être renseignées dans config.py.')
    if any('\n' in value or '\r' in value for value in (key, secret, token or '')):
        raise ValueError('Identifiants invalides : retour à la ligne.')

    # Préparer les deux contenus avant toute modification.
    parser = configparser.RawConfigParser()
    if credentials_path.exists():
        parser.read_string(credentials_path.read_text(encoding='utf-8'))
    if parser.has_section(PROFILE):
        for name, value in [('aws_access_key_id', key), ('aws_secret_access_key', secret),
                            ('aws_session_token', token)]:
            existing = parser.get(PROFILE, name, fallback=None)
            if existing and existing != value:
                raise ValueError('Le profil s3-audio-reader existe avec des clés différentes ; aucun remplacement effectué.')
    else:
        parser.add_section(PROFILE)
    parser.set(PROFILE, 'aws_access_key_id', key)
    parser.set(PROFILE, 'aws_secret_access_key', secret)
    if token:
        parser.set(PROFILE, 'aws_session_token', token)
    buffer = io.StringIO()
    parser.write(buffer)

    lines = source.splitlines(keepends=True)
    for node in reversed(assignments):
        lines[node.lineno - 1:node.end_lineno] = []
    updated = ''.join(lines).rstrip() + (
        '\n\n# Identifiants privés dans ~/.aws/credentials (sans AWS CLI).\n'
        f'AWS_PROFILE = "{PROFILE}"\n'
        'AWS_ACCESS_KEY_ID = None\n'
        'AWS_SECRET_ACCESS_KEY = None\n'
        'AWS_SESSION_TOKEN = None\n')
    ast.parse(updated)

    credentials_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    credentials_path.parent.chmod(0o700)
    atomic_write(credentials_path, buffer.getvalue())  # Fichier privé, mode 600.
    # Ne retirer les clés du code qu'après la sauvegarde réussie.
    atomic_write(config_path, updated)


def main():
    try:
        migrate(Path(__file__).resolve().parent / 'config.py', Path.home() / '.aws' / 'credentials')
    except PermissionError:
        print("Échec : accès refusé. Exécuter ce script avec votre utilisateur sur la VM ; config.py conserve ses clés.")
        return 1
    except (OSError, ValueError, SyntaxError, configparser.Error):
        print("Échec : migration impossible. Vérifier les fichiers et l'absence de profil existant avec d'autres clés.")
        return 1
    print('Migration réussie : clés dans ~/.aws/credentials (600), dossier .aws (700).')
    print('config.py utilise le profil s3-audio-reader. Redémarrer le serveur Python.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
