# S3 Audio Explorer

Application Flask servie par Waitress pour explorer un bucket S3 unique et télécharger tous les types de fichiers vers un dossier local fixé dans `config.py`. Trois transferts au maximum, file d'attente, progression en octets et annulation individuelle. La navigation et les transferts fonctionnent indépendamment.

## Démarrage

Python 3.10 ou supérieur. Depuis le dossier du projet :

```bash
.venv/bin/python -m pip install --no-cache-dir -r requirements.txt
.venv/bin/python run.py
```

Ouvrir **http://127.0.0.1:8080**. L'accès réel à AWS S3 est activé par défaut. Renseigner le nom du bucket et les paramètres AWS ci-dessous avant de parcourir les fichiers. Le nom `mon-bucket-audio` est un exemple à remplacer, pas un bucket détecté dans votre compte.

Les fichiers sont enregistrés sur la machine qui exécute Python, dans `downloads/` par défaut. Sélectionner des fichiers dans plusieurs dossiers puis cliquer sur « Télécharger la sélection ». La sélection persiste pendant la navigation. Les transferts se poursuivent si l'onglet est fermé ; rouvrir l'application permet de retrouver leur état tant que le serveur tourne.

La taille du bucket apparaît automatiquement à côté de son nom, avec la date de la mesure quotidienne CloudWatch. La lecture utilise `cloudwatch:GetMetricStatistics` (à autoriser avec `Resource: "*"` en plus des droits S3), les mêmes identifiants et la région configurée. Elle additionne les dimensions de stockage de `BucketSizeBytes` pour le dernier jour disponible sur sept jours, y compris les anciennes versions, les surcoûts et les uploads multipart incomplets. Aucun parcours des fichiers S3 n'est nécessaire. Une mesure obtenue est conservée une heure côté serveur ; recharger la page déclenche sa lecture. Si CloudWatch est inaccessible, « Taille indisponible » apparaît et son infobulle précise la raison ; un appel réussi sans données affiche « Aucune mesure CloudWatch » et n'est pas mis en cache. La navigation reste utilisable. En simulation, la taille est calculée localement.

## Configuration et AWS

Modifier `config.py` :

- `BUCKET` : nom du bucket unique.
- `DESTINATION` : dossier de destination absolu (par défaut, sous le projet).
- `SIMULATE_S3 = False` : accès réel avec boto3 (valeur par défaut). Mettre `True` uniquement pour tester sans AWS.
- `AWS_REGION` : région AWS.
- `AWS_PROFILE` : profil AWS nommé, ou `None` pour utiliser la chaîne standard de boto3 (notamment `AWS_PROFILE` dans l’environnement).
- `HOST`, `PORT` : écoute HTTP, locale par défaut.

Redémarrer après modification. boto3 utilise sa chaîne habituelle d'identifiants : variables d'environnement, profil AWS existant ou rôle IAM. Les secrets ne sont pas transmis au navigateur. Les seules permissions S3 nécessaires sont `s3:ListBucket` sur le bucket et `s3:GetObject` sur les objets autorisés. L'application ne propose aucun upload ni suppression S3.

L'arborescence est conservée : `Musique/Jazz/Piano.wav` devient `downloads/Musique/Jazz/Piano.wav`. Les noms dangereux (`..`, chemins absolus, segments vides, antislashs) et les liens symboliques sortant de la destination sont rejetés. Les fichiers sont écrits dans des fichiers temporaires uniques, puis remplacés atomiquement à la fin : un fichier déjà présent est remplacé uniquement après un transfert réussi. Une annulation ou une erreur conserve l'ancienne version et supprime le fichier temporaire. Un même objet déjà en cours ou en attente n'est pas ajouté une seconde fois.

Tous les types de fichiers sont affichés et téléchargeables, y compris les archives, documents, fichiers sans extension et fichiers vides. Tous les préfixes du bucket sont navigables et les résultats S3 sont paginés automatiquement côté serveur. Les objets terminant par `/` servent de marqueurs de dossier et ne sont pas téléchargés comme fichiers. L'accès reste soumis aux permissions du compte AWS.

Exemple de configuration pour un compte déjà authentifié :

```python
BUCKET = "nom-reel-du-bucket"
SIMULATE_S3 = False
AWS_REGION = "eu-west-3"
AWS_PROFILE = "mon-profil"  # ou None pour la configuration AWS habituelle
```

Si vous utilisez un profil SSO, authentifiez-vous avec votre outil AWS habituel avant de lancer l'application (par exemple `aws sso login --profile mon-profil`). Pour des identifiants temporaires fournis par variables d'environnement, conserver `AWS_PROFILE = None` et fournir également `AWS_SESSION_TOKEN` si nécessaire. Pour une configuration sans AWS CLI, voir ci-dessous. L'application affiche une erreur de lecture si le profil, les identifiants ou les droits sont absents ; elle ne bascule jamais automatiquement en simulation.

## Identifiants sans AWS CLI

Renseigner les champs optionnels de `config.py` avec une clé IAM limitée à la lecture du bucket :

```python
AWS_ACCESS_KEY_ID = "VOTRE_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY = "VOTRE_SECRET_ACCESS_KEY"
AWS_SESSION_TOKEN = None  # Renseigner aussi le jeton pour des clés temporaires.
```

Ces clés prennent priorité sur `AWS_PROFILE` et les identifiants de l'environnement. Les deux champs de clé sont obligatoires ensemble. Laisser les trois champs à `None` pour utiliser la chaîne habituelle de boto3. AWS CLI n'est pas nécessaire.

Les clés seront stockées en clair : conserver `config.py` privé, ne pas le partager ni le committer avec des secrets. Utiliser un utilisateur IAM dédié, sans permissions supplémentaires. Redémarrer avec `.venv/bin/python run.py` après modification.

## Déplacer les clés hors du projet

Si les clés sont déjà dans `config.py`, lancer avec votre utilisateur sur la machine du serveur :

```bash
.venv/bin/python setup_credentials.py
```

Ce script déplace les clés dans `~/.aws/credentials`, profil `s3-audio-reader`, avec un dossier en mode 700 et un fichier en mode 600. Après sauvegarde réussie, il retire les clés de `config.py` et configure le profil. Les autres profils sont conservés ; un profil du même nom avec des clés différentes bloque la migration. Il n'affiche aucun secret et ne nécessite pas AWS CLI. Redémarrer ensuite le serveur. Cette migration ne remplace pas une rotation de clé compromise.

## Diagnostic de la liste S3

Sans AWS CLI, exécuter `.venv/bin/python check_s3.py` depuis le projet. Ce diagnostic utilise les identifiants configurés et effectue une lecture limitée à dix entrées à la racine. Il affiche le nombre de fichiers et de dossiers, ou le code d'erreur, sans afficher les secrets ni les noms des objets. Il ne télécharge et ne modifie aucun objet.

Après toute modification de configuration ou du code, redémarrer le serveur Python puis recharger la page. L'API `/api/browse` fournit la liste du préfixe courant ou une erreur HTTP 502 avec un diagnostic ; une erreur AWS n'est pas une liste vide.

## Simulation facultative

Mettre `SIMULATE_S3 = True` dans `config.py`. Le simulateur fonctionne sans identifiants ni connexion AWS et propose sept fichiers WAV valides et un document texte ; le délai artificiel permet d'observer la progression et d'annuler les transferts.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests sans réseau AWS : API Flask, navigation sans filtre de type, téléchargement de fichiers non audio, choix du profil AWS, pagination boto3 via Stubber, téléchargement et arborescence, progression, plafond de trois transferts, file d'attente, annulation en attente et en cours, transferts incomplets, erreurs et chemins dangereux.

## Limites d'exploitation

Usage local : aucune authentification, conserver l'écoute sur `127.0.0.1`. La file et l'historique vivent en mémoire ; ils ne sont pas restaurés après redémarrage. Une fermeture brutale peut laisser des fichiers `.part`, qui peuvent être supprimés manuellement. L'annulation d'une lecture réseau en cours prend effet quand cette lecture rend la main (ou à son expiration). Le serveur est lancé dans un seul processus afin de partager la file entre les requêtes ; Waitress traite les requêtes sur plusieurs threads. Une liste très volumineuse est chargée intégralement pour le préfixe affiché.
