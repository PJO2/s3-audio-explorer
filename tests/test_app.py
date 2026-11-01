import io
import threading
import time

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from s3_audio_explorer import create_app
from s3_audio_explorer.storage import S3Storage, SimulatedStorage
from s3_audio_explorer.transfers import TransferManager


@pytest.fixture(autouse=True)
def isolate_local_credentials(monkeypatch):
    import config
    for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'):
        monkeypatch.setattr(config, name, None)


@pytest.fixture
def storage():
    store = SimulatedStorage()
    store.objects = {'Musique/été.wav': b'abc' * 100, 'root.MP3': b'mp3', 'notes.txt': b'text', 'README': b'no extension', 'data.bin': b'\x00\xff', 'empty': b''}
    return store


@pytest.fixture
def app(tmp_path, storage):
    application = create_app({'TESTING': True, 'DESTINATION': tmp_path,
                              'SIMULATION_DELAY': 0, 'CHUNK_SIZE': 16}, storage)
    yield application
    application.extensions['transfers'].shutdown()


def finish(manager, ids):
    for job_id in ids:
        future = manager.jobs[job_id]['future']
        if not future.cancelled():
            future.result(timeout=5)


def until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    pytest.fail('Condition non atteinte avant le délai')


def test_navigation_and_interface(app):
    client = app.test_client()
    assert b'S3 Audio Explorer' in client.get('/').data
    assert client.get('/static/app.js').status_code == 200
    root = client.get('/api/browse').json
    assert root['folders'] == ['Musique/']
    assert [item['key'] for item in root['files']] == ['README', 'data.bin', 'empty', 'notes.txt', 'root.MP3']
    assert client.get('/api/browse?prefix=Musique/').json['files'][0]['key'] == 'Musique/été.wav'
    assert client.get('/api/browse?prefix=Musique').status_code == 400


def test_download_preserves_tree_and_duplicate_selection(app, tmp_path, storage):
    client = app.test_client()
    response = client.post('/api/transfers', json={'keys': ['Musique/été.wav', 'Musique/été.wav']})
    assert response.status_code == 202
    assert len(response.json['ids']) == 1
    finish(app.extensions['transfers'], response.json['ids'])
    job = client.get('/api/transfers').json['transfers'][0]
    assert job['status'] == 'completed'
    assert job['downloaded'] == job['total'] == 300
    assert (tmp_path / 'Musique/été.wav').read_bytes() == storage.objects['Musique/été.wav']
    assert not list(tmp_path.rglob('*.part'))


@pytest.mark.parametrize('payload', [None, {}, {'keys': []}, {'keys': 'root.MP3'},
    {'keys': [123]}, {'keys': ['']}, {'keys': ['../escape.wav']},
    {'keys': ['/escape.wav']}, {'keys': ['x/../../escape.wav']},
    {'keys': ['x\\escape.wav']}, {'keys': ['x//escape.wav']}, {'keys': ['x/./escape.wav']}])
def test_reject_invalid_batches(app, payload):
    assert app.test_client().post('/api/transfers', json=payload).status_code == 400
    assert app.extensions['transfers'].snapshot() == []


def test_reject_symlink_escape(app, tmp_path):
    (tmp_path / 'outside').symlink_to(tmp_path.parent, target_is_directory=True)
    response = app.test_client().post('/api/transfers', json={'keys': ['outside/escape.wav']})
    assert response.status_code == 400


def test_missing_object_fails_without_partial(app, tmp_path):
    response = app.test_client().post('/api/transfers', json={'keys': ['absent.wav']})
    finish(app.extensions['transfers'], response.json['ids'])
    assert app.extensions['transfers'].snapshot()[0]['status'] == 'failed'
    assert not list(tmp_path.rglob('*.part'))
    assert app.test_client().post('/api/transfers/missing/cancel').status_code == 404
    assert app.test_client().delete('/api/transfers').status_code == 405


class GatedStorage:
    def __init__(self):
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = self.maximum = 0

    def list(self, prefix):
        return [], []

    def open(self, key):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        owner = self

        class Body(io.BytesIO):
            def read(self, size=-1):
                assert owner.release.wait(5)
                return super().read(size)

            def close(self):
                if not self.closed:
                    with owner.lock:
                        owner.active -= 1
                super().close()

        return Body(b'abcdefgh'), 8


def test_three_workers_queue_cancel_and_navigation(tmp_path):
    storage = GatedStorage()
    app = create_app({'TESTING': True, 'DESTINATION': tmp_path, 'CHUNK_SIZE': 2,
                      'SIMULATION_DELAY': 0}, storage)
    manager = app.extensions['transfers']
    try:
        client = app.test_client()
        ids = client.post('/api/transfers', json={'keys': [f'{i}.wav' for i in range(5)]}).json['ids']
        until(lambda: storage.active == 3)
        assert [job['status'] for job in manager.snapshot()].count('queued') == 2
        assert client.get('/api/browse').status_code == 200
        # Un deuxième ajout du même objet actif réutilise le transfert existant.
        assert manager.enqueue(['0.wav']) == [ids[0]]
        assert client.post(f'/api/transfers/{ids[4]}/cancel').status_code == 200
        assert manager.jobs[ids[4]]['status'] == 'cancelled'
        assert client.post(f'/api/transfers/{ids[0]}/cancel').status_code == 200
        storage.release.set()
        finish(manager, ids)
        assert storage.maximum == 3
        assert manager.jobs[ids[0]]['status'] == 'cancelled'
        assert not (tmp_path / '0.wav').exists()
        assert not (tmp_path / '4.wav').exists()
        assert (tmp_path / '3.wav').read_bytes() == b'abcdefgh'
        assert not list(tmp_path.rglob('*.part'))
    finally:
        storage.release.set()
        manager.shutdown()


def test_progress_and_cancel_keeps_existing_file(tmp_path, storage):
    target = tmp_path / 'root.MP3'
    target.write_bytes(b'previous')
    storage.objects['root.MP3'] = b'a' * 100
    manager = TransferManager(storage, tmp_path, chunk_size=1, delay=.02)
    try:
        ids = manager.enqueue(['root.MP3'])
        until(lambda: 0 < manager.snapshot()[0]['downloaded'] < 100)
        assert manager.snapshot()[0]['total'] == 100
        manager.cancel(ids[0])
        finish(manager, ids)
        assert manager.snapshot()[0]['status'] == 'cancelled'
        assert target.read_bytes() == b'previous'
        assert not list(tmp_path.rglob('*.part'))
    finally:
        manager.shutdown()


def test_incomplete_transfer_keeps_existing_file(tmp_path):
    class IncompleteStorage:
        def open(self, key):
            return io.BytesIO(b'abc'), 10
    (tmp_path / 'track.wav').write_bytes(b'previous')
    manager = TransferManager(IncompleteStorage(), tmp_path)
    try:
        finish(manager, manager.enqueue(['track.wav']))
        assert manager.snapshot()[0]['status'] == 'failed'
        assert (tmp_path / 'track.wav').read_bytes() == b'previous'
        assert not list(tmp_path.rglob('*.part'))
    finally:
        manager.shutdown()


def test_s3_pagination_and_stream_without_network():
    storage = S3Storage('audio-bucket', 'eu-west-3')
    storage.client = boto3.client('s3', region_name='eu-west-3',
                                 aws_access_key_id='test', aws_secret_access_key='test')
    with Stubber(storage.client) as stub:
        stub.add_response('list_objects_v2', {
            'IsTruncated': True, 'NextContinuationToken': 'next',
            'CommonPrefixes': [{'Prefix': 'albums/'}],
            'Contents': [{'Key': 'a.wav', 'Size': 3}, {'Key': 'folder/', 'Size': 0}],
        }, {'Bucket': 'audio-bucket', 'Prefix': '', 'Delimiter': '/'})
        stub.add_response('list_objects_v2', {
            'IsTruncated': False, 'Contents': [{'Key': 'b.wav', 'Size': 4}],
        }, {'Bucket': 'audio-bucket', 'Prefix': '', 'Delimiter': '/', 'ContinuationToken': 'next'})
        assert storage.list('') == (['albums/'], [{'key': 'a.wav', 'size': 3}, {'key': 'b.wav', 'size': 4}])
        stub.add_response('get_object', {'Body': StreamingBody(io.BytesIO(b'abc'), 3), 'ContentLength': 3},
                          {'Bucket': 'audio-bucket', 'Key': 'a.wav'})
        body, total = storage.open('a.wav')
        assert body.read() == b'abc' and total == 3
        body.close()
        stub.assert_no_pending_responses()


def test_browse_failure_is_reported(app, storage):
    def fail(prefix):
        raise RuntimeError('unavailable')
    storage.list = fail
    assert app.test_client().get('/api/browse').status_code == 502


@pytest.mark.parametrize('key', ['notes.txt', 'README', 'data.bin', 'empty'])
def test_download_any_file_type(app, tmp_path, storage, key):
    client = app.test_client()
    response = client.post('/api/transfers', json={'keys': [key]})
    assert response.status_code == 202
    finish(app.extensions['transfers'], response.json['ids'])
    assert (tmp_path / key).read_bytes() == storage.objects[key]
    job = client.get('/api/transfers').json['transfers'][0]
    assert job['status'] == 'completed'
    assert job['downloaded'] == job['total'] == len(storage.objects[key])


def test_real_aws_client_uses_configured_profile_lazily(monkeypatch, tmp_path):
    from unittest.mock import Mock
    session = Mock()
    factory = Mock(return_value=session)
    monkeypatch.setattr(boto3, 'Session', factory)
    session.client.return_value.get_paginator.return_value.paginate.return_value = [
        {'Contents': [{'Key': 'archive.zip', 'Size': 12}]}
    ]
    app = create_app({'TESTING': True, 'DESTINATION': tmp_path, 'SIMULATE_S3': False,
                      'BUCKET': 'my-bucket', 'AWS_PROFILE': 'my-profile', 'AWS_REGION': 'eu-west-1'})
    try:
        client = app.test_client()
        assert client.get('/').status_code == 200
        factory.assert_not_called()
        for _ in range(2):
            assert client.get('/api/browse').json['files'] == [{'key': 'archive.zip', 'size': 12}]
        factory.assert_called_once_with(profile_name='my-profile')
        session.client.assert_called_once_with('s3', region_name='eu-west-1')
        session.client.return_value.get_paginator.return_value.paginate.assert_called_with(
            Bucket='my-bucket', Prefix='', Delimiter='/')
    finally:
        app.extensions['transfers'].shutdown()


def test_missing_aws_profile_does_not_prevent_ui_startup(monkeypatch, tmp_path):
    from botocore.exceptions import ProfileNotFound
    def missing_session(**kwargs):
        raise ProfileNotFound(profile='missing')
    monkeypatch.setattr(boto3, 'Session', missing_session)
    app = create_app({'TESTING': True, 'DESTINATION': tmp_path, 'SIMULATE_S3': False,
                      'AWS_PROFILE': 'missing'})
    try:
        client = app.test_client()
        assert client.get('/').status_code == 200
        assert client.get('/api/browse').status_code == 502
    finally:
        app.extensions['transfers'].shutdown()


@pytest.mark.parametrize('token', [None, 'temporary-token'])
def test_explicit_credentials_override_profile(monkeypatch, tmp_path, token):
    from unittest.mock import Mock
    session = Mock()
    factory = Mock(return_value=session)
    monkeypatch.setattr(boto3, 'Session', factory)
    session.client.return_value.get_paginator.return_value.paginate.return_value = [{}]
    app = create_app({'DESTINATION': tmp_path, 'SIMULATE_S3': False,
                      'AWS_PROFILE': 'unused-profile', 'AWS_ACCESS_KEY_ID': 'test-key',
                      'AWS_SECRET_ACCESS_KEY': 'test-secret', 'AWS_SESSION_TOKEN': token})
    try:
        assert app.test_client().get('/api/browse').status_code == 200
        factory.assert_called_once_with(aws_access_key_id='test-key',
                                        aws_secret_access_key='test-secret',
                                        aws_session_token=token)
    finally:
        app.extensions['transfers'].shutdown()


@pytest.mark.parametrize('credentials', [
    {'access_key': 'test-key'}, {'secret_key': 'test-secret'},
    {'session_token': 'temporary-token'}])
def test_incomplete_credentials_do_not_fall_back(monkeypatch, credentials):
    from unittest.mock import Mock
    factory = Mock()
    monkeypatch.setattr(boto3, 'Session', factory)
    storage = S3Storage('bucket', 'eu-north-1', **credentials)
    with pytest.raises(ValueError, match='Renseigner ensemble'):
        storage.list('')
    factory.assert_not_called()


@pytest.mark.parametrize('code, expected', [
    ('AccessDenied', 's3:ListBucket'),
    ('InvalidAccessKeyId', "clé d'accès"),
    ('SignatureDoesNotMatch', 'clé secrète'),
    ('ExpiredToken', 'expiré'),
    ('NoSuchBucket', "n'existe pas"),
])
def test_aws_error_is_not_reported_as_empty_bucket(app, storage, code, expected):
    from botocore.exceptions import ClientError
    def fail(prefix):
        raise ClientError({'Error': {'Code': code, 'Message': 'private details'}},
                          'ListObjectsV2')
    storage.list = fail
    response = app.test_client().get('/api/browse')
    assert response.status_code == 502
    assert expected in response.json['error']
    assert code in response.json['error']
    assert 'files' not in response.json
    assert 'private details' not in response.json['error']


def test_network_failure_is_not_reported_as_empty_bucket(app, storage):
    from botocore.exceptions import EndpointConnectionError
    def fail(prefix):
        raise EndpointConnectionError(endpoint_url='https://s3.eu-north-1.amazonaws.com')
    storage.list = fail
    response = app.test_client().get('/api/browse')
    assert response.status_code == 502
    assert 'Connexion à S3 impossible' in response.json['error']
    assert 'files' not in response.json
